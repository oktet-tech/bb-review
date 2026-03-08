#!/usr/bin/env bash
# Install bb-review using uv into an isolated tool environment.
set -euo pipefail

if ! command -v uv &>/dev/null; then
    echo "error: uv is not installed. See https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "Installing bb-review from ${REPO_DIR} ..."
uv tool install --from "${REPO_DIR}" bb-review --force

BIN_DIR="${UV_TOOL_BIN_DIR:-${HOME}/.local/bin}"

echo ""
echo "Installed successfully."
echo ""
echo "Make sure the tool bin directory is in your PATH:"
echo ""
echo "  export PATH=\"\$PATH:${BIN_DIR}\""
echo ""
echo "Add the line above to your ~/.zshrc or ~/.bashrc to make it permanent."
