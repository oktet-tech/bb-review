#!/bin/bash
#
# Setup PostgreSQL with pgvector for CocoIndex
#
# Usage:
#   ./scripts/setup-cocoindex-db.sh [start|stop|status|logs]
#
# Environment variables:
#   COCOINDEX_DB_PORT - PostgreSQL port (default: 5432)
#   COCOINDEX_DB_PASSWORD - Database password (default: cocoindex)
#   COCOINDEX_DB_USER - Database user (default: cocoindex)
#   COCOINDEX_DB_NAME - Database name (default: cocoindex)
#

set -e

CONTAINER_NAME="cocoindex-postgres"
VOLUME_NAME="cocoindex-pgdata"

# Configuration with defaults
DB_PORT="${COCOINDEX_DB_PORT:-5432}"
DB_PASSWORD="${COCOINDEX_DB_PASSWORD:-cocoindex}"
DB_USER="${COCOINDEX_DB_USER:-cocoindex}"
DB_NAME="${COCOINDEX_DB_NAME:-cocoindex}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

check_docker() {
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed. Please install Docker first."
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        log_error "Docker daemon is not running. Please start Docker."
        exit 1
    fi
}

start_db() {
    check_docker
    
    # Check if container already exists
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
            log_info "Container '${CONTAINER_NAME}' is already running."
            show_connection_info
            return 0
        else
            log_info "Starting existing container '${CONTAINER_NAME}'..."
            docker start "${CONTAINER_NAME}"
        fi
    else
        log_info "Creating and starting PostgreSQL container with pgvector..."
        
        # Create volume if it doesn't exist
        if ! docker volume ls --format '{{.Name}}' | grep -q "^${VOLUME_NAME}$"; then
            docker volume create "${VOLUME_NAME}"
            log_info "Created volume '${VOLUME_NAME}'"
        fi
        
        # Start container
        docker run -d \
            --name "${CONTAINER_NAME}" \
            -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
            -e POSTGRES_USER="${DB_USER}" \
            -e POSTGRES_DB="${DB_NAME}" \
            -p "${DB_PORT}:5432" \
            -v "${VOLUME_NAME}:/var/lib/postgresql/data" \
            --restart unless-stopped \
            pgvector/pgvector:pg17
        
        log_info "Container '${CONTAINER_NAME}' created."
    fi
    
    # Wait for PostgreSQL to be ready
    log_info "Waiting for PostgreSQL to be ready..."
    for i in {1..30}; do
        if docker exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" &> /dev/null; then
            log_info "PostgreSQL is ready!"
            show_connection_info
            return 0
        fi
        sleep 1
    done
    
    log_error "PostgreSQL did not become ready in time."
    exit 1
}

stop_db() {
    check_docker
    
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_info "Stopping container '${CONTAINER_NAME}'..."
        docker stop "${CONTAINER_NAME}"
        log_info "Container stopped."
    else
        log_warn "Container '${CONTAINER_NAME}' is not running."
    fi
}

remove_db() {
    check_docker
    
    stop_db 2>/dev/null || true
    
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_info "Removing container '${CONTAINER_NAME}'..."
        docker rm "${CONTAINER_NAME}"
    fi
    
    read -p "Also remove data volume '${VOLUME_NAME}'? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if docker volume ls --format '{{.Name}}' | grep -q "^${VOLUME_NAME}$"; then
            docker volume rm "${VOLUME_NAME}"
            log_info "Volume removed."
        fi
    fi
    
    log_info "Cleanup complete."
}

show_status() {
    check_docker
    
    echo "Container: ${CONTAINER_NAME}"
    echo "Volume: ${VOLUME_NAME}"
    echo ""
    
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo -e "Status: ${GREEN}RUNNING${NC}"
        echo ""
        docker ps --filter "name=${CONTAINER_NAME}" --format "table {{.ID}}\t{{.Status}}\t{{.Ports}}"
    elif docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        echo -e "Status: ${YELLOW}STOPPED${NC}"
    else
        echo -e "Status: ${RED}NOT CREATED${NC}"
    fi
}

show_logs() {
    check_docker
    
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        docker logs -f "${CONTAINER_NAME}"
    else
        log_error "Container '${CONTAINER_NAME}' does not exist."
        exit 1
    fi
}

show_connection_info() {
    echo ""
    echo "Connection Information:"
    echo "  Host: localhost"
    echo "  Port: ${DB_PORT}"
    echo "  Database: ${DB_NAME}"
    echo "  User: ${DB_USER}"
    echo "  Password: ${DB_PASSWORD}"
    echo ""
    echo "Connection URL:"
    echo "  postgresql://${DB_USER}:${DB_PASSWORD}@localhost:${DB_PORT}/${DB_NAME}"
    echo ""
    echo "Environment variable for CocoIndex:"
    echo "  export COCOINDEX_DATABASE_URL=\"postgresql://${DB_USER}:${DB_PASSWORD}@localhost:${DB_PORT}/${DB_NAME}\""
}

show_help() {
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  start   - Start PostgreSQL container (default)"
    echo "  stop    - Stop PostgreSQL container"
    echo "  status  - Show container status"
    echo "  logs    - Show container logs (follow mode)"
    echo "  remove  - Stop and remove container (optionally remove data)"
    echo "  help    - Show this help message"
    echo ""
    echo "Environment variables:"
    echo "  COCOINDEX_DB_PORT     - PostgreSQL port (default: 5432)"
    echo "  COCOINDEX_DB_PASSWORD - Database password (default: cocoindex)"
    echo "  COCOINDEX_DB_USER     - Database user (default: cocoindex)"
    echo "  COCOINDEX_DB_NAME     - Database name (default: cocoindex)"
}

# Main command dispatch
case "${1:-start}" in
    start)
        start_db
        ;;
    stop)
        stop_db
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    remove|rm)
        remove_db
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
