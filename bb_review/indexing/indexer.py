"""CocoIndex-based codebase indexer with local embeddings.

This module provides codebase indexing using CocoIndex with sentence-transformers
for local embeddings (no API calls, no rate limits).

Based on: https://cocoindex.io/blogs/index-code-base-for-rag
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cocoindex
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

# Default embedding model - good balance of speed and quality
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Code file extensions to index
DEFAULT_EXTENSIONS = [
    "*.py", "*.c", "*.h", "*.cpp", "*.hpp", "*.cc",
    "*.js", "*.ts", "*.tsx", "*.jsx",
    "*.go", "*.rs", "*.java", "*.rb", "*.php",
    "*.sh", "*.bash", "*.yaml", "*.yml", "*.json",
    "*.md", "*.rst", "*.txt",
]

# Patterns to exclude
DEFAULT_EXCLUDES = [
    ".*",  # Hidden files/dirs
    "**/node_modules",
    "**/vendor",
    "**/target",
    "**/__pycache__",
    "**/build",
    "**/dist",
    "**/.git",
]


@dataclass
class IndexConfig:
    """Configuration for indexing a repository."""
    
    repo_name: str
    repo_path: str
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    chunk_size: int = 1000
    chunk_overlap: int = 300
    included_patterns: Optional[list[str]] = None
    excluded_patterns: Optional[list[str]] = None
    
    def __post_init__(self):
        if self.included_patterns is None:
            self.included_patterns = DEFAULT_EXTENSIONS
        if self.excluded_patterns is None:
            self.excluded_patterns = DEFAULT_EXCLUDES


@dataclass
class IndexResult:
    """Result of indexing operation."""
    
    repo_name: str
    status: str  # 'created', 'updated', 'unchanged', 'failed'
    file_count: int = 0
    chunk_count: int = 0
    message: Optional[str] = None


def _sanitize_name(name: str) -> str:
    """Sanitize a name for use in table/flow names."""
    return name.replace("-", "_").replace("/", "_").replace(".", "_")


@cocoindex.op.function()
def extract_extension(filename: str) -> str:
    """Extract the extension of a filename for Tree-sitter language detection."""
    return os.path.splitext(filename)[1]


def create_embedding_flow(config: IndexConfig):
    """Create a CocoIndex embedding transform flow.
    
    This is a factory function that creates the transform flow with the
    configured embedding model.
    """
    model_name = config.embedding_model
    
    @cocoindex.transform_flow()
    def code_to_embedding(text: cocoindex.DataSlice[str]) -> cocoindex.DataSlice[list[float]]:
        """Embed text using a SentenceTransformer model."""
        return text.transform(
            cocoindex.functions.SentenceTransformerEmbed(model=model_name)
        )
    
    return code_to_embedding


def create_indexing_flow(config: IndexConfig):
    """Create a CocoIndex flow for indexing a repository.
    
    Args:
        config: Index configuration
        
    Returns:
        A CocoIndex flow definition
    """
    sanitized_name = _sanitize_name(config.repo_name)
    flow_name = f"CodeIndex_{sanitized_name}"
    
    # Create the embedding transform
    code_to_embedding = create_embedding_flow(config)
    
    @cocoindex.flow_def(name=flow_name)
    def code_embedding_flow(
        flow_builder: cocoindex.FlowBuilder, 
        data_scope: cocoindex.DataScope
    ):
        """Define a flow that embeds code files into a vector database."""
        
        # Add the codebase as a source
        data_scope["files"] = flow_builder.add_source(
            cocoindex.sources.LocalFile(
                path=config.repo_path,
                included_patterns=config.included_patterns,
                excluded_patterns=config.excluded_patterns,
            )
        )
        
        # Collector for embeddings
        code_embeddings = data_scope.add_collector()
        
        # Process each file
        with data_scope["files"].row() as file:
            # Extract extension for Tree-sitter language detection
            file["extension"] = file["filename"].transform(extract_extension)
            
            # Split into chunks using Tree-sitter
            file["chunks"] = file["content"].transform(
                cocoindex.functions.SplitRecursively(),
                language=file["extension"],
                chunk_size=config.chunk_size,
                chunk_overlap=config.chunk_overlap,
            )
            
            # Process each chunk
            with file["chunks"].row() as chunk:
                # Generate embedding
                chunk["embedding"] = chunk["text"].call(code_to_embedding)
                
                # Collect to output
                code_embeddings.collect(
                    repo=config.repo_name,
                    filename=file["filename"],
                    location=chunk["location"],
                    code=chunk["text"],
                    embedding=chunk["embedding"],
                )
        
        # Export to PostgreSQL with vector index
        table_name = f"{sanitized_name}_chunks"
        code_embeddings.export(
            table_name,
            cocoindex.storages.Postgres(),
            primary_key_fields=["repo", "filename", "location"],
            vector_indexes=[
                cocoindex.VectorIndexDef(
                    field_name="embedding", 
                    metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY
                )
            ],
        )
    
    return code_embedding_flow, code_to_embedding


class CodebaseIndexer:
    """Manages codebase indexing with CocoIndex."""
    
    def __init__(self, database_url: str):
        """Initialize the indexer.
        
        Args:
            database_url: PostgreSQL connection URL
        """
        self.database_url = database_url
        self._pool: Optional[ConnectionPool] = None
        self._flows: dict[str, tuple] = {}  # repo_name -> (flow, embedding_fn)
        
        # Set CocoIndex database URL
        os.environ["COCOINDEX_DATABASE_URL"] = database_url
    
    @property
    def pool(self) -> ConnectionPool:
        """Get or create the connection pool."""
        if self._pool is None:
            self._pool = ConnectionPool(self.database_url)
        return self._pool
    
    def index_repo(self, config: IndexConfig, clear: bool = False) -> IndexResult:
        """Index a repository.
        
        Args:
            config: Index configuration
            clear: If True, clear existing index first
            
        Returns:
            IndexResult with status and counts
        """
        logger.info(f"Indexing {config.repo_name} from {config.repo_path}")
        logger.info(f"Using embedding model: {config.embedding_model}")
        
        try:
            # Create the flow first (needed for proper drop)
            flow, embedding_fn = create_indexing_flow(config)
            self._flows[config.repo_name] = (flow, embedding_fn)
            
            # Clear if requested - use CocoIndex's drop() for proper cleanup
            if clear:
                logger.info(f"Clearing existing index for {config.repo_name}")
                try:
                    flow.drop(report_to_stdout=True)
                    logger.info("Index cleared via CocoIndex drop()")
                except Exception as e:
                    logger.warning(f"Drop failed (may be first run): {e}")
            
            # Setup the flow (creates tables, indexes, etc.)
            logger.info("Setting up flow backends...")
            flow.setup(report_to_stdout=True)
            
            # Run the indexing flow (builds/updates target data)
            logger.info("Running indexing...")
            stats = flow.update()
            logger.info(f"Update stats: {stats}")
            
            # Get counts
            file_count, chunk_count = self._get_counts(config.repo_name)
            
            return IndexResult(
                repo_name=config.repo_name,
                status="updated",
                file_count=file_count,
                chunk_count=chunk_count,
            )
            
        except Exception as e:
            logger.error(f"Indexing failed: {e}")
            return IndexResult(
                repo_name=config.repo_name,
                status="failed",
                message=str(e),
            )
    
    def _clear_index(self, repo_name: str) -> None:
        """Clear the index for a repository."""
        sanitized = _sanitize_name(repo_name)
        # CocoIndex table format: codeindex_{sanitized}__{sanitized}_chunks
        chunks_table = f"codeindex_{sanitized}__{sanitized}_chunks"
        tracking_table = f"codeindex_{sanitized}__cocoindex_tracking"
        
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    # Drop the chunks table if it exists
                    cur.execute(f"DROP TABLE IF EXISTS {chunks_table} CASCADE")
                    # Also drop the tracking table
                    cur.execute(f"DROP TABLE IF EXISTS {tracking_table} CASCADE")
                conn.commit()
            logger.info(f"Cleared index tables for {repo_name}")
        except Exception as e:
            logger.warning(f"Error clearing index: {e}")
    
    def _get_counts(self, repo_name: str) -> tuple[int, int]:
        """Get file and chunk counts for a repository."""
        sanitized = _sanitize_name(repo_name)
        # CocoIndex uses format: codeindex_{flow_name}__{table_name}
        # where flow_name = CodeIndex_{sanitized} and table_name = {sanitized}_chunks
        table_name = f"codeindex_{sanitized}__{sanitized}_chunks"
        
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"""
                        SELECT 
                            COUNT(DISTINCT filename) as file_count,
                            COUNT(*) as chunk_count
                        FROM {table_name}
                        WHERE repo = %s
                    """, (repo_name,))
                    row = cur.fetchone()
                    if row:
                        return row[0], row[1]
        except Exception as e:
            logger.warning(f"Error getting counts: {e}")
        
        return 0, 0
    
    def search(
        self, 
        repo_name: str, 
        query: str, 
        top_k: int = 10,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> list[dict]:
        """Search for code matching a query.
        
        Args:
            repo_name: Repository to search
            query: Search query
            top_k: Number of results to return
            embedding_model: Model to use for query embedding
            
        Returns:
            List of results with filename, code, and score
        """
        sanitized = _sanitize_name(repo_name)
        table_name = f"codeindex_{sanitized}__{sanitized}_chunks"
        
        # Get or create embedding function for this repo
        if repo_name in self._flows:
            _, embedding_fn = self._flows[repo_name]
        else:
            # Create a temporary config to get the embedding function
            config = IndexConfig(repo_name=repo_name, repo_path=".", embedding_model=embedding_model)
            embedding_fn = create_embedding_flow(config)
        
        # Embed the query
        query_vector = embedding_fn.eval(query)
        
        # Search
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT filename, code, embedding <=> %s::vector AS distance
                    FROM {table_name}
                    WHERE repo = %s
                    ORDER BY distance
                    LIMIT %s
                """, (query_vector, repo_name, top_k))
                
                return [
                    {
                        "filename": row[0],
                        "code": row[1],
                        "score": 1.0 - row[2],
                    }
                    for row in cur.fetchall()
                ]
    
    def get_status(self) -> list[dict]:
        """Get indexing status for all repositories."""
        results = []
        
        try:
            with self.pool.connection() as conn:
                with conn.cursor() as cur:
                    # Get all CocoIndex chunk tables (format: codeindex_*__*_chunks)
                    # Note: %% is escaped % in psycopg
                    cur.execute("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name LIKE 'codeindex_%%_chunks'
                    """)
                    tables = cur.fetchall()
                    
            # Query each table in its own connection/transaction
            for (table_name,) in tables:
                try:
                    with self.pool.connection() as conn:
                        with conn.cursor() as cur:
                            # First check if table has 'repo' column (our new format)
                            cur.execute("""
                                SELECT column_name 
                                FROM information_schema.columns 
                                WHERE table_name = %s AND column_name = 'repo'
                            """, (table_name,))
                            
                            if cur.fetchone():
                                # New format with repo column
                                cur.execute(f"""
                                    SELECT 
                                        repo,
                                        COUNT(DISTINCT filename) as file_count,
                                        COUNT(*) as chunk_count
                                    FROM {table_name}
                                    GROUP BY repo
                                """)
                                
                                for row in cur.fetchall():
                                    results.append({
                                        "repo": row[0],
                                        "file_count": row[1],
                                        "chunk_count": row[2],
                                        "table": table_name,
                                    })
                            else:
                                # Old cocode-mcp format without repo column
                                # Extract repo name from table name
                                # Format: codeindex_{repo}__{repo}_chunks
                                parts = table_name.replace("codeindex_", "").split("__")
                                if parts:
                                    repo_name = parts[0]
                                    cur.execute(f"""
                                        SELECT 
                                            COUNT(DISTINCT filename) as file_count,
                                            COUNT(*) as chunk_count
                                        FROM {table_name}
                                    """)
                                    row = cur.fetchone()
                                    if row:
                                        results.append({
                                            "repo": repo_name,
                                            "file_count": row[0],
                                            "chunk_count": row[1],
                                            "table": table_name,
                                        })
                except Exception as e:
                    logger.debug(f"Error querying {table_name}: {e}")
                    continue
        except Exception as e:
            logger.warning(f"Error getting status: {e}")
        
        return results
    
    def close(self):
        """Close the connection pool."""
        if self._pool:
            self._pool.close()
            self._pool = None
