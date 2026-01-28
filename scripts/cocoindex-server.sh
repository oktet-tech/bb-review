#!/bin/bash
#
# Manage CocoIndex MCP server for code repositories
#
# Usage:
#   ./scripts/cocoindex-server.sh start <repo-name> [--rescan]
#   ./scripts/cocoindex-server.sh stop <repo-name>
#   ./scripts/cocoindex-server.sh status [repo-name]
#   ./scripts/cocoindex-server.sh logs <repo-name>
#
# Prerequisites:
#   - uv pip install cocode-mcp
#   - PostgreSQL with pgvector running (use setup-cocoindex-db.sh)
#   - COCOINDEX_DATABASE_URL environment variable set
#   - JINA_API_KEY (or OPENAI_API_KEY) for embeddings
#
# NOTE: cocode-mcp uses stdio transport, so the MCP client spawns it directly.
# This script is for manually running/testing the indexer.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${PROJECT_DIR}/config.yaml"
LOG_DIR="${HOME}/.bb_review/cocoindex"
PID_DIR="${HOME}/.bb_review/cocoindex/pids"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

ensure_dirs() {
    mkdir -p "$LOG_DIR"
    mkdir -p "$PID_DIR"
}

# Parse YAML config to get repo settings
# Usage: get_repo_config <repo-name> <field>
# Fields: local_path, mcp_port, enabled
get_repo_config() {
    local repo_name="$1"
    local field="$2"
    
    if ! command -v python3 &> /dev/null; then
        log_error "Python3 is required to parse config"
        exit 1
    fi
    
    python3 << EOF
import yaml
import sys
from pathlib import Path

config_path = Path("$CONFIG_FILE")
if not config_path.exists():
    print("", end="")
    sys.exit(0)

with open(config_path) as f:
    config = yaml.safe_load(f)

repo_name = "$repo_name"
field = "$field"

# Find the repo
for repo in config.get("repositories", []):
    if repo.get("name") == repo_name:
        if field == "local_path":
            path = repo.get("local_path", "")
            print(str(Path(path).expanduser()), end="")
        elif field == "mcp_port":
            cocoindex = repo.get("cocoindex", {})
            port = cocoindex.get("mcp_port") if cocoindex else None
            if port is None:
                port = config.get("cocoindex", {}).get("default_port", 3033)
            print(port, end="")
        elif field == "enabled":
            cocoindex = repo.get("cocoindex", {})
            enabled = cocoindex.get("enabled", False) if cocoindex else False
            if not enabled:
                enabled = config.get("cocoindex", {}).get("enabled", False)
            print("true" if enabled else "false", end="")
        break
EOF
}

# Get database URL from config or environment
get_database_url() {
    if [ -n "$COCOINDEX_DATABASE_URL" ]; then
        echo "$COCOINDEX_DATABASE_URL"
        return
    fi
    
    python3 << EOF
import yaml
import os
from pathlib import Path

config_path = Path("$CONFIG_FILE")
if not config_path.exists():
    print("postgresql://cocoindex:cocoindex@localhost:5432/cocoindex", end="")
else:
    with open(config_path) as f:
        config = yaml.safe_load(f)
    url = config.get("cocoindex", {}).get("database_url", "postgresql://cocoindex:cocoindex@localhost:5432/cocoindex")
    print(url, end="")
EOF
}

# List all configured repos
list_repos() {
    python3 << EOF
import yaml
from pathlib import Path

config_path = Path("$CONFIG_FILE")
if not config_path.exists():
    print("No config file found")
else:
    with open(config_path) as f:
        config = yaml.safe_load(f)
    
    for repo in config.get("repositories", []):
        name = repo.get("name", "unknown")
        cocoindex = repo.get("cocoindex", {})
        enabled = cocoindex.get("enabled", False) if cocoindex else False
        if not enabled:
            enabled = config.get("cocoindex", {}).get("enabled", False)
        port = cocoindex.get("mcp_port") if cocoindex else None
        if port is None:
            port = config.get("cocoindex", {}).get("default_port", 3033)
        
        status = "enabled" if enabled else "disabled"
        print(f"  {name}: port={port}, cocoindex={status}")
EOF
}

get_pid_file() {
    local repo_name="$1"
    echo "${PID_DIR}/${repo_name}.pid"
}

get_log_file() {
    local repo_name="$1"
    echo "${LOG_DIR}/${repo_name}.log"
}

is_running() {
    local repo_name="$1"
    local pid_file=$(get_pid_file "$repo_name")
    
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

start_server() {
    local repo_name="$1"
    local rescan="$2"
    
    ensure_dirs
    
    # Check if already running
    if is_running "$repo_name"; then
        local pid=$(cat "$(get_pid_file "$repo_name")")
        log_warn "Server for '${repo_name}' is already running (PID: ${pid})"
        return 0
    fi
    
    # Get repo config
    local repo_path=$(get_repo_config "$repo_name" "local_path")
    local port=$(get_repo_config "$repo_name" "mcp_port")
    local enabled=$(get_repo_config "$repo_name" "enabled")
    
    if [ -z "$repo_path" ]; then
        log_error "Repository '${repo_name}' not found in config"
        exit 1
    fi
    
    if [ "$enabled" != "true" ]; then
        log_warn "CocoIndex is not enabled for '${repo_name}' in config"
        log_info "Enable it by adding 'cocoindex.enabled: true' to the repo config"
    fi
    
    if [ ! -d "$repo_path" ]; then
        log_error "Repository path does not exist: ${repo_path}"
        exit 1
    fi
    
    # Check if cocode-mcp is installed
    if ! command -v cocode &> /dev/null; then
        log_error "cocode-mcp is not installed"
        log_info "Install it with: uv pip install cocode-mcp"
        exit 1
    fi
    
    # Get database URL
    local db_url=$(get_database_url)
    
    local log_file=$(get_log_file "$repo_name")
    local pid_file=$(get_pid_file "$repo_name")
    
    log_info "Starting cocode-mcp for '${repo_name}'..."
    log_info "  Repository: ${repo_path}"
    log_info "  Log file: ${log_file}"
    log_info ""
    log_info "NOTE: cocode-mcp uses stdio transport - the MCP client spawns it directly."
    log_info "This command runs the server interactively for testing/debugging."
    log_info ""
    
    # cocode-mcp is stdio-based, but we can run it for testing
    # Set working directory to the repo path so it indexes that repo
    cd "$repo_path"
    
    # Export environment
    export COCOINDEX_DATABASE_URL="$db_url"
    
    # Check for embedding API key (can be passed via COCOINDEX_EMBEDDING_API_KEY from CLI)
    if [ -z "$JINA_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ -z "$OPENROUTER_API_KEY" ] && [ -z "$COCOINDEX_EMBEDDING_API_KEY" ]; then
        log_warn "No embedding API key set - embeddings may fail"
        log_info "Configure embedding_api_key in config.yaml, or set OPENROUTER_API_KEY"
    fi
    
    # Use COCOINDEX_EMBEDDING_API_KEY if set (from CLI reading config.yaml)
    if [ -n "$COCOINDEX_EMBEDDING_API_KEY" ]; then
        export OPENAI_API_KEY="$COCOINDEX_EMBEDDING_API_KEY"
        export OPENAI_BASE_URL="${COCOINDEX_EMBEDDING_BASE_URL:-https://openrouter.ai/api/v1}"
        export EMBEDDING_PROVIDER="${COCOINDEX_EMBEDDING_PROVIDER:-openai}"
        export EMBEDDING_MODEL="${COCOINDEX_EMBEDDING_MODEL:-mistralai/codestral-embed-2505}"
        log_info "Using embeddings from config (${EMBEDDING_PROVIDER}: ${EMBEDDING_MODEL})"
    # Fallback to OPENROUTER_API_KEY env var
    elif [ -n "$OPENROUTER_API_KEY" ]; then
        export OPENAI_API_KEY="$OPENROUTER_API_KEY"
        export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
        export EMBEDDING_PROVIDER="openai"
        export EMBEDDING_MODEL="mistralai/codestral-embed-2505"
        log_info "Using OpenRouter for embeddings (Codestral Embed)"
    fi
    
    log_info "Running cocode (press Ctrl+C to stop)..."
    log_info "Database: $db_url"
    
    # Run interactively (stdio-based server)
    cocode 2>&1 | tee "$log_file"
}

stop_server() {
    local repo_name="$1"
    local pid_file=$(get_pid_file "$repo_name")
    
    if ! is_running "$repo_name"; then
        log_warn "Server for '${repo_name}' is not running"
        rm -f "$pid_file"
        return 0
    fi
    
    local pid=$(cat "$pid_file")
    log_info "Stopping server for '${repo_name}' (PID: ${pid})..."
    
    kill "$pid" 2>/dev/null || true
    
    # Wait for graceful shutdown
    for i in {1..10}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 1
    done
    
    # Force kill if still running
    if kill -0 "$pid" 2>/dev/null; then
        log_warn "Server did not stop gracefully, forcing..."
        kill -9 "$pid" 2>/dev/null || true
    fi
    
    rm -f "$pid_file"
    log_info "Server stopped"
}

show_status() {
    local repo_name="$1"
    
    ensure_dirs
    
    if [ -n "$repo_name" ]; then
        # Status for specific repo
        local pid_file=$(get_pid_file "$repo_name")
        local port=$(get_repo_config "$repo_name" "mcp_port")
        
        echo "Repository: ${repo_name}"
        echo "Port: ${port}"
        
        if is_running "$repo_name"; then
            local pid=$(cat "$pid_file")
            echo -e "Status: ${GREEN}RUNNING${NC} (PID: ${pid})"
            echo "MCP endpoint: http://localhost:${port}/mcp"
        else
            echo -e "Status: ${RED}STOPPED${NC}"
        fi
    else
        # Status for all repos
        echo "Configured repositories:"
        list_repos
        echo ""
        echo "Running servers:"
        
        local found=false
        for pid_file in "$PID_DIR"/*.pid; do
            if [ -f "$pid_file" ]; then
                local name=$(basename "$pid_file" .pid)
                if is_running "$name"; then
                    local pid=$(cat "$pid_file")
                    local port=$(get_repo_config "$name" "mcp_port")
                    echo -e "  ${name}: ${GREEN}RUNNING${NC} (PID: ${pid}, port: ${port})"
                    found=true
                fi
            fi
        done
        
        if [ "$found" = false ]; then
            echo "  (none)"
        fi
    fi
}

show_logs() {
    local repo_name="$1"
    local log_file=$(get_log_file "$repo_name")
    
    if [ ! -f "$log_file" ]; then
        log_error "No log file found for '${repo_name}'"
        exit 1
    fi
    
    tail -f "$log_file"
}

show_help() {
    echo "Usage: $0 <command> [repo-name] [options]"
    echo ""
    echo "Commands:"
    echo "  start <repo>    - Start CocoIndex MCP server for a repository"
    echo "                    Options: --rescan (force rebuild index)"
    echo "  stop <repo>     - Stop the server for a repository"
    echo "  status [repo]   - Show server status (all repos if none specified)"
    echo "  logs <repo>     - Follow log output for a repository"
    echo "  help            - Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 start te-dev              # Start server for te-dev repo"
    echo "  $0 start te-dev --rescan     # Start with fresh index"
    echo "  $0 stop te-dev               # Stop server"
    echo "  $0 status                    # Show all servers"
    echo ""
    echo "Prerequisites:"
    echo "  1. Install: uv pip install cocode-mcp"
    echo "  2. Start PostgreSQL: ./scripts/setup-cocoindex-db.sh start"
    echo "  3. Set env: COCOINDEX_DATABASE_URL, JINA_API_KEY (or OPENAI_API_KEY)"
    echo ""
    echo "Note: cocode-mcp is stdio-based - the MCP client spawns it directly."
    echo "This script is mainly for testing/debugging the indexer."
}

# Main command dispatch
case "${1:-help}" in
    start)
        if [ -z "$2" ]; then
            log_error "Repository name required"
            echo "Usage: $0 start <repo-name> [--rescan]"
            exit 1
        fi
        start_server "$2" "$3"
        ;;
    stop)
        if [ -z "$2" ]; then
            log_error "Repository name required"
            echo "Usage: $0 stop <repo-name>"
            exit 1
        fi
        stop_server "$2"
        ;;
    status)
        show_status "$2"
        ;;
    logs)
        if [ -z "$2" ]; then
            log_error "Repository name required"
            echo "Usage: $0 logs <repo-name>"
            exit 1
        fi
        show_logs "$2"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        log_error "Unknown command: $1"
        show_help
        exit 1
        ;;
esac
