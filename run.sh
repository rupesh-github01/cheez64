#!/usr/bin/env bash
# ==============================================================================
# Personal Chess Coach — Unified Service Runner & CLI
# ==============================================================================
# Usage:
#   ./run.sh            Start both FastAPI backend and Next.js frontend
#   ./run.sh all        Start both services concurrently
#   ./run.sh api        Start FastAPI backend only (port 8000)
#   ./run.sh web        Start Next.js frontend only (port 3000)
#   ./run.sh test       Run full Python test suite and Next.js build
#   ./run.sh check      Verify environment, dependencies, and ports
# ==============================================================================

set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# Load .env file if present
if [ -f "$REPO_ROOT/.env" ]; then
  # Export non-comment lines
  export $(grep -v '^#' "$REPO_ROOT/.env" | xargs -0 2>/dev/null || true)
fi

PORT_API="${API_PORT:-8000}"
PORT_WEB=3000

# Color helpers
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }

check_python() {
  if ! command -v python3 &>/dev/null; then
    error "Python 3 is required but was not found in PATH."
    exit 1
  fi
}

check_node() {
  if ! command -v node &>/dev/null; then
    error "Node.js is required but was not found in PATH."
    exit 1
  fi
  if ! command -v npm &>/dev/null; then
    error "npm is required but was not found in PATH."
    exit 1
  fi
}

check_port_in_use() {
  local port=$1
  if lsof -Pi :"$port" -sTCP:LISTEN -t &>/dev/null; then
    return 0 # in use
  fi
  return 1 # available
}

kill_existing_port() {
  local port=$1
  local name=$2
  local pids
  pids=$(lsof -ti :"$port" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    warn "Port $port ($name) is currently occupied by PID(s): $pids."
    read -r -p "Terminate existing process(es) to proceed? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
      kill -9 $pids 2>/dev/null || true
      sleep 1
      ok "Port $port freed."
    else
      error "Cannot start $name while port $port is in use."
      exit 1
    fi
  fi
}

check_env() {
  info "Checking system environment and dependencies..."
  check_python
  check_node

  local py_ver
  py_ver=$(python3 --version 2>&1)
  ok "$py_ver detected."

  local node_ver
  node_ver=$(node --version)
  ok "Node.js $node_ver detected."

  if [ ! -d "$REPO_ROOT/web/node_modules" ]; then
    warn "Frontend node_modules not found. Installing web dependencies..."
    (cd "$REPO_ROOT/web" && npm install)
    ok "Frontend dependencies installed."
  else
    ok "Frontend node_modules present."
  fi

  if command -v stockfish &>/dev/null; then
    ok "Stockfish binary found in PATH: $(which stockfish)"
  elif [ -x "/opt/homebrew/bin/stockfish" ]; then
    ok "Stockfish binary found at /opt/homebrew/bin/stockfish"
  else
    warn "Stockfish binary not detected in PATH or /opt/homebrew/bin/stockfish."
    warn "Chess analysis will require Stockfish to be installed or STOCKFISH_PATH configured."
  fi

  if [ -f "$REPO_ROOT/data/library.db" ]; then
    ok "SQLite library database found at data/library.db."
  else
    info "data/library.db not yet created; it will initialize on first run."
  fi
}

start_api() {
  check_python
  kill_existing_port "$PORT_API" "FastAPI Backend"
  info "Starting FastAPI backend on http://localhost:$PORT_API ..."
  export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"
  exec python3 -m uvicorn api.main:app --host "${API_HOST:-0.0.0.0}" --port "$PORT_API" --reload
}

start_web() {
  check_node
  kill_existing_port "$PORT_WEB" "Next.js Frontend"
  if [ ! -d "$REPO_ROOT/web/node_modules" ]; then
    info "Installing web dependencies..."
    (cd "$REPO_ROOT/web" && npm install)
  fi
  info "Starting Next.js frontend on http://localhost:$PORT_WEB ..."
  cd "$REPO_ROOT/web"
  exec npm run dev
}

start_all() {
  check_env
  echo ""
  kill_existing_port "$PORT_API" "FastAPI Backend"
  kill_existing_port "$PORT_WEB" "Next.js Frontend"

  info "Starting Personal Chess Coach services..."
  export PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}"

  # Launch API in background
  python3 -m uvicorn api.main:app --host "${API_HOST:-0.0.0.0}" --port "$PORT_API" &
  API_PID=$!

  # Launch Frontend in background
  (cd "$REPO_ROOT/web" && npm run dev) &
  WEB_PID=$!

  # Cleanup trap on Ctrl+C or kill
  cleanup() {
    echo ""
    info "Shutting down Personal Chess Coach services..."
    kill "$API_PID" "$WEB_PID" 2>/dev/null || true
    wait "$API_PID" "$WEB_PID" 2>/dev/null || true
    ok "All services stopped cleanly."
    exit 0
  }
  trap cleanup SIGINT SIGTERM EXIT

  echo ""
  ok "FastAPI running at: http://localhost:$PORT_API (PID $API_PID)"
  ok "Next.js running at: http://localhost:$PORT_WEB (PID $WEB_PID)"
  info "Press Ctrl+C to stop all services."
  echo ""

  # Wait for both processes
  wait "$API_PID" "$WEB_PID"
}

run_tests() {
  check_env
  info "Running Python test suite..."
  python3 -m pytest -v
  ok "Python test suite passed!"

  echo ""
  info "Building Next.js production bundle..."
  (cd "$REPO_ROOT/web" && npm run build)
  ok "Next.js build succeeded!"
}

# Subcommand dispatch
MODE="${1:-all}"

case "$MODE" in
  all|"")
    start_all
    ;;
  api|backend)
    start_api
    ;;
  web|frontend)
    start_web
    ;;
  check)
    check_env
    ;;
  test|tests)
    run_tests
    ;;
  help|-h|--help)
    echo "Usage: ./run.sh [all | api | web | check | test]"
    echo ""
    echo "Commands:"
    echo "  all     Start both FastAPI and Next.js concurrently (default)"
    echo "  api     Start FastAPI backend only (port 8000)"
    echo "  web     Start Next.js frontend only (port 3000)"
    echo "  check   Verify system prerequisites and ports"
    echo "  test    Run pytest suite and Next.js production build"
    ;;
  *)
    error "Unknown command: $MODE"
    echo "Run './run.sh --help' for usage."
    exit 1
    ;;
esac
