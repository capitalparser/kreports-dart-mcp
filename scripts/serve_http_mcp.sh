#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8765}"
MCP_PATH="${MCP_PATH:-/mcp}"

export KREPORTS_RUNTIME_MODE="${KREPORTS_RUNTIME_MODE:-readonly}"

exec kreports serve-http \
  --host "$HOST" \
  --port "$PORT" \
  --path "$MCP_PATH" \
  --stateless
