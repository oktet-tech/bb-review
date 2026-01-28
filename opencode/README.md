# OpenCode MCP Configuration Templates

This directory contains OpenCode configuration templates for different MCP (Model Context Protocol) setups.

## Templates

### `mcp-filesystem.json`
Basic filesystem MCP using `@anthropic-ai/mcp-filesystem`.
- **Pros**: No setup required, works immediately
- **Cons**: No semantic search, just file access
- **Use for**: Quick access to repo files without indexing

### `mcp-cocode.json`
Semantic code search using `cocode-mcp` with Jina embeddings.
- **Pros**: Semantic search, finds code by meaning, free tier available
- **Cons**: Requires PostgreSQL + Jina API key setup
- **Use for**: Deep code understanding and review

### `mcp-cocode-openrouter.json`
Semantic code search using OpenRouter's embeddings API (Codestral Embed).
- **Pros**: Use your existing OpenRouter API key for embeddings
- **Cons**: Requires PostgreSQL setup
- **Use for**: If you already have OpenRouter and don't want another API key

### `mcp-combined.json`
Both semantic and filesystem MCP servers.
- **Pros**: Best of both worlds
- **Cons**: More complex setup

## Repo-Specific Configs

### `ts-te-mcp`
Configuration for te-dev repository (Test Environment API).
Used by `bb-review repos mcp-setup` command.

### `te-ts-reviewer`
OpenCode agent definition for API review.

### `te-review-command`
OpenCode command for triggering API review.

## Setup

### For cocode-mcp (semantic search):

1. **Install cocode-mcp:**
   ```bash
   uv pip install cocode-mcp
   ```

2. **Start PostgreSQL with pgvector:**
   ```bash
   ./scripts/setup-cocoindex-db.sh start
   ```

3. **Set API key for embeddings:**
   ```bash
   # Option 1: Jina (recommended, free tier available)
   export JINA_API_KEY="jina_..."
   
   # Option 2: OpenAI
   export OPENAI_API_KEY="sk-..."
   ```

4. **Copy and customize template:**
   ```bash
   cp opencode/mcp-cocode.json ~/repos/my-repo/opencode.json
   # Edit the file to set repo-specific values
   ```

### For filesystem MCP:

Just copy and edit the path:
```bash
cp opencode/mcp-filesystem.json ~/repos/my-repo/opencode.json
# Edit the path in the command array
```

## Environment Variables

| Variable | Description | Required for |
|----------|-------------|--------------|
| `OPENROUTER_API_KEY` | OpenRouter API key | cocode with OpenRouter embeddings |
| `JINA_API_KEY` | Jina AI API key (free tier at jina.ai) | cocode with Jina embeddings |
| `COCOINDEX_DATABASE_URL` | PostgreSQL connection | cocode |

**Tip**: If you have an OpenRouter key, use `mcp-cocode-openrouter.json` to get
embeddings via `mistralai/codestral-embed-2505` without needing another API key.

## Usage with OpenCode

Once configured, run OpenCode in the repository:
```bash
cd ~/repos/my-repo
opencode
```

The MCP tools will be available for semantic code search.
