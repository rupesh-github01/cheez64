# ♟️ Personal Chess Coach

Evidence-based chess coaching system with local Stockfish analysis, tactical episode detection, opening repertoire tracking, personalized player profiling, and an interactive LLM-powered conversational coach with an offline deterministic fallback.

---

## Architecture Overview

```
Next.js 16 + Tailwind CSS (Port 3000)
        │
        ▼ (HTTP REST)
FastAPI Backend (Port 8000)
        │
        ├─► SQLite GameLibrary (data/library.db)
        ├─► Local Stockfish Engine (UCI subprocess)
        ├─► Deterministic Tactical & Recommendation Analyzers
        └─► Conversational Coach:
              ├─► Free Offline Deterministic Fallback (Default)
              └─► Optional External LLM (OpenAI-compatible)
```

---

## Prerequisites

1. **Python 3.10+** (Python 3.11, 3.12, 3.14 supported)
2. **Node.js 18+** & **npm**
3. **Stockfish Chess Engine**:
   - macOS: `brew install stockfish`
   - Debian/Ubuntu: `sudo apt install stockfish`
   - Windows: Download binary from [stockfishchess.org](https://stockfishchess.org/download/) and add to PATH or set `STOCKFISH_PATH`.

---

## Quick Start

### 1. Clone & Set Up Python Environment

```bash
git clone https://github.com/rupesh-github01/cheez64.git
cd cheez64

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install backend dependencies
pip install -r requirements.txt
```

### 2. Set Up Frontend Dependencies

```bash
cd web
npm install
cd ..
```

### 3. Verify System Health

Run the built-in diagnostic checker:

```bash
./run.sh check
```

Output:
```text
[OK] Python 3.x.x detected.
[OK] Node.js vx.x.x detected.
[OK] Frontend node_modules present.
[OK] Stockfish binary found.
[OK] SQLite library database found.
```

### 4. Launch Services

Launch both the FastAPI backend and Next.js frontend with one command:

```bash
./run.sh
```

- **Frontend (Web App)**: [http://localhost:3000](http://localhost:3000)
- **FastAPI API**: [http://localhost:8000](http://localhost:8000)
- **API Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)

To stop all services cleanly, press `Ctrl+C`.

---

## Independent Service Startup

You can also run services independently in separate terminals:

```bash
# Terminal 1: Backend only
./run.sh api
# or directly: python3 -m uvicorn api.main:app --port 8000 --reload

# Terminal 2: Frontend only
./run.sh web
# or directly: cd web && npm run dev
```

---

## Environment Configuration

Copy `.env.example` to `.env` to customize settings:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `CHESS_COACH_DB` | `data/library.db` | Path to SQLite game library database |
| `STOCKFISH_PATH` | System PATH / `/opt/homebrew/bin/stockfish` | Path to Stockfish UCI executable |
| `API_PORT` | `8000` | FastAPI server port |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend API URL for frontend fetch |
| `CHESS_COACH_LLM_PROVIDER` | `deterministic` | Coach engine (`deterministic` or `openai`) |
| `OPENAI_API_KEY` | *(None)* | Optional API key for external LLM |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model identifier for external provider |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Base URL for OpenAI-compatible API |

> [!NOTE]
> **Free Offline Coach**: The coach works completely offline out of the box using existing Stockfish analysis and deterministic rule-grounded synthesis. Setting `OPENAI_API_KEY` is completely optional and may incur third-party API usage costs.

---

---

## Complete Analysis Workflow

The application supports a complete, decoupled 4-step pipeline for historical chess analysis:

```
1. Import Games ──► 2. Enqueue Games ──► 3. Background Worker ──► 4. Interactive Review
   (Chess.com/Lichess)   (Priority/Config Queue)   (Stockfish Evaluator)   (UI & Coach)
```

### Step 1: Import Games from Chess.com or Lichess

```bash
# Bulk import games with automatic checkpointing and bounded memory
python -m src.importers chess.com <username> --from 2026-01
python -m src.importers lichess <username> --from 2026-01
```

### Step 2: Enqueue Unanalyzed Games into the Queue

```bash
# Queue all unanalyzed games with custom engine depth and priority
python -m src.queue_cli enqueue-all --depth 16 --multipv 3

# Or queue specific games
python -m src.queue_cli enqueue game_abc123 game_def456 --priority 5

# Inspect queue status
python -m src.queue_cli status
python -m src.queue_cli list --status PENDING
```

### Step 3: Start the Stockfish Analysis Worker

```bash
# Start background worker via unified script
./run.sh worker

# Or run worker directly with custom limits
python -m src.worker_cli run --once
python -m src.worker_cli run --max-jobs 20 --poll-interval 2.0
```

### Step 4: Review Completed Games & Chat with Coach

Launch the web app (`./run.sh` or `./run.sh web`) and visit [http://localhost:3000/games](http://localhost:3000/games) to explore move evaluations, critical positions, tactical findings, and talk with the personalized chess coach.

---

### Detect Openings & Build Repertoire

```bash
# Detect ECO codes and opening variations for all library games
python src/library.py openings-detect

# View player opening repertoire
python src/library.py repertoire <player_name>
```

---

## Testing & Quality Assurance

### Run All Tests & Production Build

```bash
./run.sh test
```

Or run test runners individually:

```bash
# Run full Python test suite (183 tests)
pytest -v

# Run Next.js production build
cd web && npm run build
```

---

## Troubleshooting

### 1. Port 8000 or 3000 Already in Use
`./run.sh` automatically detects port conflicts and prompts to terminate conflicting processes. Alternatively:
```bash
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
lsof -ti :3000 | xargs kill -9 2>/dev/null || true
```

### 2. Stockfish Binary Not Found
If Stockfish is installed in a non-standard path:
```bash
export STOCKFISH_PATH=/path/to/stockfish
```
Verify with:
```bash
python3 -c "import os, shutil; print(os.environ.get('STOCKFISH_PATH', shutil.which('stockfish')))"
```

### 3. Frontend Shows "Cannot Connect to Backend API"
Ensure FastAPI is running on port 8000:
```bash
curl http://localhost:8000/api/health
# Expected: {"status":"ok"}
```

### 4. Game Shows "Analysis Not Available"
The game is present in SQLite but has not been analyzed yet. Run analysis via:
```bash
python src/analyzer.py --pgn data/games/<game>.pgn
```

---

## License

MIT License. Stockfish is licensed under the GNU General Public License v3.

## Position indexing (Segment 14)

Games are stored immediately and scheduled for position indexing in the same
transaction. A separate CLI replays the mainline without Stockfish. Positions
include the starting board and the final recorded board; equivalent positions
across move orders share a versioned identity. Standard chess is supported;
other variants remain accessible as games but are marked unsupported for indexing.

This segment has **not migrated the existing personal `data/library.db`**. Use a
separate database for validation. The position CLI requires `--db` and refuses
the repository's personal database path (including symlink/hardlink aliases).
Opening another database with GameLibrary installs the additive index schema;
historical games are discovered by explicit backfill, never by an API read.

```sh
# Use your own separate test database, already containing imported games.
python -m src.position_cli status --db /tmp/chess-position-test.db
python -m src.position_cli backfill --db /tmp/chess-position-test.db --max-games 100 --max-seconds 30
python -m src.position_cli run --db /tmp/chess-position-test.db --follow --max-games 1000 --max-seconds 60
python -m src.position_cli reindex --db /tmp/chess-position-test.db --game-id GAME_ID
python -m src.position_cli retry --db /tmp/chess-position-test.db --game-id GAME_ID
```

`backfill` reconciles historical/missing scheduling rows and processes pending
work; repeat it until coverage is complete. `run` processes already scheduled
work; `--once` handles at most one game. `reindex` and `retry` schedule work for
a subsequent run. Failed games do not block other work; failed reindexing retains
the last completed occurrence set. Each game publishes atomically, so restarting
an interrupted command cannot duplicate occurrences. CLI time limits are checked
between bounded games; publication/lock acquisition may finish after that limit.
Replay limits are 2 MiB PGN and 10,000 mainline plies per game, with explicit
failure rather than truncation. The CLI does not automatically start with the API.

Configure `CHESS_COACH_DB` to a separate database when starting an API for testing.
New GET endpoints:

- `/api/positions/status`
- `/api/positions/lookup?fen=FOUR_OR_SIX_FIELD_FEN&variant=standard`
- `/api/positions/{position_id}`
- `/api/positions/{position_id}/games?limit=50&after_game=CURSOR`
- `/api/positions/{position_id}/occurrences?limit=50&after_game=CURSOR&after_ply=PLY`
- `/api/positions/{position_id}/moves`

All support optional exact case-insensitive `player` and
`player_color=white|black|either` filters. SQLite NOCASE provides ASCII case
folding. Games are paged once per distinct game; the occurrence endpoint provides
all matching plies with bounded pages. Opening labels are stored game-level labels.

Every successful response exposes `coverage`, including `partial_index`, pending/missing/
failed/unsupported counts, indexed-game count and `absence_is_conclusive`.
A zero-result lookup with partial coverage does not establish that no library
game contains the position. All queries for one response share a SQLite read
snapshot; separate requests may observe newer commits. These new routes open
read-only connections and never create/migrate an index or run a backfill.

Move frequency counts **occurrences with outgoing moves**. Outcome statistics
count **distinct games per move** and use White's perspective. Only wins, losses
and draws enter `completed_games`; unfinished/unknown results are separate.
Win rate excludes draws from the numerator; score rate awards half a point per
draw. Rates with no completed games are null. A repeated position can contribute
multiple move occurrences but only one result per move; distinct-game counts
across different moves are not additive. A final occurrence with no recorded next
move does not imply checkmate or a position with no legal moves.

The key omits move counters and legally irrelevant en-passant targets, but retains
side to move and effective castling rights. It is an exploration identity, not a
rule-complete game state or a cache of reusable engine evaluations.

To reproduce the independent temporary-database benchmark (no engine/network):

```sh
python scripts/benchmark_positions.py --include-local --scales 50 1000 5000 --output segment_14_position_benchmarks.json
```

The supervisor enforces 120 seconds and 512 MiB RSS per scale, with a 600-second
overall budget. Process RSS inspection requires permission to execute `ps`.
Synthetic results and the small checked-in real-PGN sample are reported separately.
See `segment_14_position_index_report.md` for validation, measurements and limits.
