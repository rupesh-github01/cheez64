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
See `plan_report/segment_14_position_index_report.md` for validation, measurements and limits.

## Engine-free player profiles (Segment 15)

The new player endpoints describe imported metadata and played moves without
Stockfish, coaching generation, LLM calls, indexing writes or a statistical cache.
They use SQLite read-only connections and one consistent snapshot per response.
Existing dashboard/coaching endpoints retain their behavior.

Use an explicit recorded source and exact player label:

```text
GET /api/players/fast-profile?source=lichess&username=Alice
GET /api/players/overview?source=lichess&username=Alice
GET /api/players/trends?source=lichess&username=Alice
GET /api/players/repertoire?source=lichess&username=Alice
GET /api/players/time-management?source=lichess&username=Alice
GET /api/players/tendencies?source=lichess&username=Alice
GET /api/players/coverage?source=lichess&username=Alice
```

Chess.com/Lichess labels match exactly with ASCII case folding; local/upload
labels are case-sensitive. Sources are never automatically combined into one
person. Games matching both sides are reported as ambiguous and excluded from
color-sensitive statistics. The source means recorded game origin, not verified
account ownership; cross-source deduplication may retain only the first origin.

Optional filters are `color`, ISO `date_from`/`date_to`, and exact `time_control`.
`top_k` caps display lists (1–50) without truncating corpus-level aggregates.
Replay defaults to at most 20,000 games and 30 seconds, checked between bounded
games. `completeness` and per-category `coverage` disclose failures, unsupported
variants and budget exhaustion. Metadata denominators remain complete if replay
is partial. No-result identities return 404; matched games with no completed
results return null rates. Empty or incompatible databases return 503.

Win/score rates use completed results from the selected player's perspective.
Move frequencies use observed player moves; position continuation frequencies use
occurrences and outcomes count distinct games per move. Position views are scoped
to the same source/player and show index coverage independently of replay coverage.
The ranked position window is relative plies 1–24; initial ply-zero occurrences
are omitted while later repetitions remain visible. An absent/incomplete index
does not stop metadata or move replay and is not evidence a position never occurred.

Metrics identify OBSERVED versus HEURISTIC provenance, availability status, units,
method, numerator/denominator and sample coverage. Development, phase, weighted
material, early queen movement, opening diversity and pawn-placement signatures
are descriptive rules, never verified mistakes or personality diagnoses. Stored
analysis availability is counted separately; its JSON/conclusions are not used.

Reported `%emt` annotations provide elapsed-time observations. Clocks alone do
not produce durations by default. `clock_convention=post_increment` explicitly
asserts that a supported single-stage clock is reported after its increment;
estimates then use the previous clock for the **same** player plus increment
minus current clock. These estimates are HEURISTIC. Missing adjacent clocks,
negative differences, unknown conventions, metadata/header conflicts and stage/
delay/hourglass/daily controls do not produce inferred durations. Explicit EMT
can still be used. Supported estimates never imply a fast move was a blunder.
Exact duration medians are omitted above 4,096 observations to bound storage;
means, fixed bins and counts retain all observations.

Personal SQLite databases, WAL/SHM sidecars and `data/backups/` are now ignored by
Git. The current personal database was backed up and verified before Segment 15
and removed from Git tracking without deleting the working file. Existing history
was not rewritten. No Segment 15 schema or personal-database migration occurred.
Continue using an explicit temporary `CHESS_COACH_DB` for validation; starting the
legacy application against its default database can still run existing startup
recovery/schema checks.

The standalone profiling benchmark creates all databases in temporary directories:

```sh
python scripts/benchmark_fast_profiles.py --scales 50 1000 --samples 30 --include-local --output small.json
python scripts/benchmark_fast_profiles.py --scales 5000 --samples 3 --output large.json
```

It measures the complete initial HTTP report for the selected player's games,
reports position-index preprocessing separately, enforces time/RSS budgets, and
checks concurrent imports/reads and queue heartbeats without running an engine.
See `plan_report/segment_15_fast_profiling_report.md` for verified results and limitations.

## Evaluation cache (Segment 16)

The evaluation cache is independent of GameLibrary and position indexing. It
never starts Stockfish, schedules analysis, ingests historical analysis JSON or
changes coaching/fast-profile results. Cache initialization is explicit:

```sh
python -m src.evaluation_cli init --cache /path/to/separate/evaluations.sqlite3
export CHESS_COACH_EVAL_CACHE_DB=/path/to/separate/evaluations.sqlite3
python -m src.evaluation_cli status --cache /path/to/separate/evaluations.sqlite3
python -m src.evaluation_cli maintain --cache /path/to/separate/evaluations.sqlite3
```

For this checkout, an operator-approved one-time setup can use:

```sh
.venv/bin/python -m src.evaluation_cli init --cache "$PWD/data/evaluations.sqlite3"
export CHESS_COACH_EVAL_CACHE_DB="$PWD/data/evaluations.sqlite3"
```

This creates only the **independent, initially empty** `data/evaluations.sqlite3`
(and SQLite sidecars as needed), never the game library. Do not run it against an
existing unknown/incompatible file, library or backup. Initialization does not
produce scores or PVs: a compatible empty cache returns `CACHE_MISS`. Evidence
must already have been legitimately ingested or come from an explicitly opted-in
cloud request. Native Stockfish is not started by this setup.

Persist the canonical absolute cache path in the ignored `.env` if using
`./run.sh`; otherwise export it in the environment launching the API. Restart the
API yourself after changing configuration. `./run.sh` warns about unset/missing
cache storage but **never initializes or repairs it**. `/api/evaluations/status`
is a cache-only GET reporting exact setup/read errors, `read_only`, cloud
enablement and whether an operator key is configured (never its value). Read-only
API mode permits cache lookup but refuses cloud resolution. Cloud also requires
server enablement and a matching private operator key of at least 16 characters;
there is no automatic provider request on game navigation.

Library/backup paths, sidecars and aliases are rejected. Do not point this at
`data/library.db`. Missing/uninitialized/incompatible cache storage is reported
as unavailable; reads do not initialize it. The existing application's legacy
startup recovery still writes its configured GameLibrary; new cache read routes
are independent and do not make the whole application read-only.

Local routes:

- `GET /api/evaluations/lookup?fen=<six-field-FEN>&allow_advisory=true`
- `POST /api/evaluations/lookup-batch` with `{"items":[{"context":{"fen":"..."},"allow_advisory":true}]}` (up to 100 items).
- `GET /api/evaluations/status` for cache counts, policy, cooldown and size.

`POST /api/evaluations/resolve` takes a single lookup object; `/resolve-batch`
takes at most five items. Cloud access is disabled by default. Enabling it
requires `CHESS_COACH_EVAL_CLOUD_ENABLED=true`, an operator-controlled
`CHESS_COACH_EVAL_RESOLVE_KEY` of at least 16 characters, the matching
`X-Evaluation-Key` request header, and each lookup's explicit `allow_cloud:true`
and `allow_advisory:true`. Keep this operator key out of frontend bundles. These
routes use fixed-origin, bounded Lichess HTTP with no redirects or engine fallback.
All cooperating processes must use one shared cache to coordinate quotas; do not
enable independent clients behind the same egress without shared coordination.

Scores are typed `cp`, signed `mate` in moves, or proven `terminal_checkmate`,
always relative to White. Zero centipawns differs from a missing evaluation.
`TRUSTED_CONTEXT_MATCH` requires complete verified-root history matching the
stored local context. `ADVISORY_CONTEXT_UNVERIFIED` explicitly excludes draw and
repetition guarantees, including all Lichess records. A lookup with
`require_trusted:true` requires `root_fen`, complete legal UCI `moves` and
`root_history_complete:true` (standard initial root only); cloud evidence cannot
satisfy it. Standalone local FENs also remain advisory about history.

Different engine/configuration families are not ranked against each other;
`CONFIG_REQUIRED` asks the caller to supply a returned `config_hash` or
`family_hash`. Within a compatible family, achieved depth, nodes, observation
time and digest break ties. Minimum depth/PVs, partial/stale acceptance and
source constraints are explicit lookup fields. Coverage describes the request,
never all library games or all chess positions.

`EvaluationCache.ingest(Evaluation(...))` is the future local producer interface,
not an HTTP ingestion endpoint. It validates engine name/version/build/NNUE,
explicit complete applied options including Threads/Hash, search limits,
context and legal UCI PVs. It trusts the producer's metadata attestation; it does
not run or inspect an engine. The existing analyzer is not connected to it.

Configure validated capacity/retention settings using JSON in
`CHESS_COACH_EVAL_LIMITS` (unknown/unsafe settings fail closed). Defaults are
50,000 records, 100,000 contexts, 256 MiB total main/WAL/SHM budget with 4 MiB
admission headroom, four records/config, 16 configs/context, 32 records/context,
10,000 negative entries, 90-day retention and 30-day cloud freshness. Scale
limits can increase up to their documented model bounds without schema changes.
Admission rejects writes at capacity; explicit bounded maintenance removes old
records/orphans. Maintenance reports its effective maximum eight-record batch;
repeat it to drain more work. Existing readers remain available under pressure.

Bodies are limited to 128 KiB, batch output to 2 MiB, histories to 1,000 plies,
and PVs to five lines of 64 plies. Cloud attempts have an eight-second total
deadline; batches have 15 seconds and no scheduled continuation. Shared rolling
limits allow one in-flight request, at least five seconds between starts,
12/minute and 60/hour; Retry-After/cooldowns can defer work longer.

The independent deterministic benchmark uses temporary databases, synthetic
scores, mocked HTTP and mocked queue completion only:

```sh
python scripts/benchmark_evaluations.py --scales 50 1000 5000 --output /tmp/evaluation-benchmarks.json
```

See `plan_report/segment_16_evaluation_cache_report.md` for measurements, provenance,
validation and limitations. No Segment 17 fallback or scoring integration exists.

## Progressive analysis (Segment 17)

Progressive analysis has an independent SQLite store and versioned partial
artifacts. It never replaces game-analysis JSON, calculates legacy CPL, marks a
GameLibrary game `ANALYZED`, or changes coaching/fast-profile outputs. Prepare-only
jobs work without an engine profile and publish structural observations, quiet
coverage candidates, cache coverage and explicit unsearched-position limitations.
Games over 1,000 plies are rejected, preserving evaluation-cache history identity.

Initialize explicitly against separate paths:

```sh
python -m src.progressive_cli init --store /path/to/progressive.sqlite3 --library /path/to/library.sqlite3
python -m src.progressive_cli enqueue --store /path/to/progressive.sqlite3 --library /path/to/library.sqlite3 --request-file request.json
python -m src.progressive_cli prepare --store /path/to/progressive.sqlite3 --library /path/to/library.sqlite3 --max-jobs 100
python -m src.progressive_cli status --store /path/to/progressive.sqlite3 --library /path/to/library.sqlite3
```

An enqueue request is `{"game_ids":["game_..."],"idempotency_key":"request-1"}`;
`prepare_only` defaults true. Reusing a key with another payload is rejected.
Analysis requests set `prepare_only:false` and a registered `profile_id`. Batch
admission accepts at most 100 games/2 MiB aggregate PGN snapshots per call, up to
5,000 games sharing one immutable batch budget; appending chunks does not renew
credits. A job without a registered profile waits with `WAITING_PROFILE`.

Set `CHESS_COACH_PROGRESSIVE_DB`, `CHESS_COACH_DB` and, optionally,
`CHESS_COACH_EVAL_CACHE_DB` for the API. New routes are under
`/api/progressive-analysis`: POST `/jobs`, `/jobs/batch`, `/jobs/{id}/cancel`,
`/jobs/{id}/retry`; GET `/jobs`, `/jobs/{id}`, `/jobs/{id}/result`,
`/games/{id}/progress`, `/health`. Writes require a server
`CHESS_COACH_PROGRESSIVE_KEY` of at least 16 characters and matching
`X-Progressive-Key` header. Reads never initialize stores or launch engines;
result pages expose live work separately from immutable phase artifacts.

Engine dispatch is disabled by default. Explicit CLI `work --enable-engine`
requires an operator command file, exclusive-host attestation, no observed active
legacy worker and successful bundled fake-UCI safety probes. The Unix guardian
retains the host slot until its owned child exits/reaps; a live orphan keeps the
slot blocked. Unsupported hosts/accounting fail closed. Runtime proof binds to
code/platform. One managed engine, Threads=1 and Hash=64 MiB are supported; a
512 MiB owned-process RSS watchdog includes worker, guardian and child. The
host lock is shared across CLI stores for the same OS user. Legacy/external
engines do not honor it, so exclusive operation remains an operator requirement.

Profiles use measured build/NNUE identities, full applied options and reset-policy
provenance. Real profile provenance starts `pcis-progressive:v1:reset-per-context`;
build is `sha256:<digest>`, NNUE is either `embedded:sha256:<build digest>` or a
measured external network digest. `register --profile-file` registers a profile;
`--simulation` explicitly labels a synthetic profile. `work --mock` is restricted
to temporary stores/libraries/caches and cannot use a real profile. Mock scores
and timings are never evidence of Stockfish correctness or throughput.

Screen/medium/deep searches request depths 10/14/18 with 0.10/0.30/1.00 s caps;
qualifying minima 8/12/16 and actual achieved depth are reported separately.
Per-game screening/deepening is bounded; remaining budgets can produce partial
outcomes. Leases, unique interests/shared work, persisted retry backoff and
transactional reservations survive interruption. Cache publication uses an outbox
and does not lose accepted evidence if the cache is full. Cloud cache evidence is
advisory only; progressive code makes no HTTP requests. Explicit cloud population
remains the separately opted-in Segment 16 API's responsibility.

Configure validated limits with `CHESS_COACH_PROGRESSIVE_LIMITS` JSON. Defaults:
1 GiB storage with 8 MiB admission headroom, 50,000 jobs, 10,000 active/500,000
retained work rows, 90-day retention, 30 s game/300 s batch budgets. Cleanup is
explicit and bounded; aggregate batch cost audit remains after job expiry.
`maintain` removes one expired source/artifact payload per transaction; repeat as
needed. Readers can delay WAL reclamation, and capacity rejects new writes.

Validation harness (temporary databases and synthetic adapters only):

```sh
python scripts/benchmark_progressive_analysis.py --scales 50 1000 5000 --output /private/tmp/progressive-benchmarks.json
```

See `plan_report/segment_17_progressive_analysis_report.md` for measured preparation/cache,
deduplication, mock scheduling, recovery, fairness, safety tests and limitations.
No Segment 18 or multi-engine research is included.

## Unified player dashboard (Segment 18)

`/dashboard` selects one exact imported account and platform. The historical
analyzed-game dashboard remains at `/dashboard/legacy` with its original broader
selection rules. The new view shows metadata first; Openings and Tendencies share
one bounded replay response. Dates, color and **raw** time-control filters are
restored from the URL. No accounts are linked across platforms.

New read-only collection contracts:

- `GET /api/players/identities?q=Hero&source=lichess&limit=20` returns bounded
  source/account choices and an opaque `next_cursor` (use `after` to continue).
- `GET /api/players/games?source=lichess&username=Hero&limit=20` returns exact-scoped
  rows, `selected_total`, `matching_total`, stored legacy-analysis/status counts,
  generation time and `next_cursor`. Optional color/date/time-control, result,
  legacy status, reported ECO, position ID and bounded example IDs intersect that
  cohort. A cursor belongs to its complete selection. Position-filtered responses
  expose `position_coverage`; incomplete indexing cannot prove absence.
- Fast-profile opening groups add `evidence_game_ids` (at most five) and
  `examples_are_bounded`; all existing statistical calculations are unchanged.

For a non-mutating dashboard API deployment, explicitly start with
`CHESS_COACH_READ_ONLY=true` and `CHESS_COACH_DB=/absolute/path/to/existing.db`.
This skips import/queue startup recovery and blocks mutation and management
routes. The sole allowed POST is the existing **local-only** evaluation
`lookup-batch`. Ordinary management startup retains recovery behavior. Legacy
review GET connections now use `GameLibrary(..., read_only=True)` (`mode=ro`,
`query_only=ON`, no schema or WAL-mode initialization). Missing libraries fail;
missing evaluation/progressive stores are never created by dashboard reads.
This is an operational read-only boundary, not authentication for public hosting.

The dashboard never starts engines, calls cloud resolution or schedules jobs.
A selected game's existing progressive artifacts remain separate from legacy
analysis. The PGN browser uses a disposable worker (2 MiB / 10,000-ply input,
five-second watchdog), supports ply zero, and sends an exact legally replayed
history only on explicit local-cache lookup. The 1,000-ply evaluation-context cap
remains enforced; setup roots cannot request trusted complete-history evidence.
Cloud-origin cached evidence requires an explicit advisory opt-in and cannot
silently satisfy trusted coverage. Native dispatch stays disabled by default;
the existing 512 MiB resource ceiling is unchanged.

Frontend reuse is in-tab only: eight responses / 2 MiB, metadata freshness
15 seconds and replay freshness 60 seconds, five-minute hard eviction, manual
refresh, no polling or persistent statistics cache. Replay requests are globally
serialized in the tab; an already-running server replay may finish after
navigation. This is not a hard HTTP latency guarantee or cross-tab scheduler.

Validation commands (no new test dependencies):

```sh
cd web
node --test tests/dashboard.mjs
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/eslint src/lib/player-dashboard.ts src/components/PlayerDashboard src/app/dashboard/page.tsx tests/dashboard.mjs
npm run build -- --webpack
```

Use temporary databases for Python tests. Segment plans and validation reports
belong in intentionally ignored `plan_report/`; never stage that directory.

## Selected-position understanding (Segment 19)

Game review includes an on-demand **Understand this position** panel for both
stored-analysis and unanalyzed PGN reviews. It follows the selected recorded ply;
variation previews are a different history and disable this panel until returning
to the game. Neither navigation nor dashboard loading computes understanding.

`GET /api/games/{game_id}/understanding` requires `ply` (including zero) and
`expected_pgn_sha256`, the lowercase SHA-256 of the exact displayed UTF-8 PGN.
An edited/deleted source returns 409/404. It validates the full bounded mainline
before presenting a selected position; malformed tails are not accepted as valid
prefixes. The existing index and stored analysis are not prerequisites.

The `position-understanding/1` response separates deterministic board facts,
heuristic interpretations, legal witnesses, before/after changes, positional
feature maps, examination prompts, detector coverage and optional engine evidence.
Pins, forks, skewer alignments and attack/defense counts do not establish forced
material gain. Mate-in-one witnesses are legal child positions that are actually
checkmate; no deeper tactical solver or static exchange evaluation is implied.

Optional `evidence=accepted_local` reads existing stores only. Context compatibility
is separate from producer admission: a cache record alone, or a CACHE_REUSE wrapper,
cannot establish non-simulated execution. The reader requires a retained accepted
non-simulated LOCAL_SEARCH publication, compatible complete history/configuration,
settlement consistency, valid PVs and achieved depth at least 16. Requested depth,
actual depth and typed White-relative cp/mate stay distinct. Synthetic fixtures
exercise these contracts in tests; no real-engine qualification is claimed.
Missing/unattested stores leave board understanding useful. No cloud resolution,
engine launch, queue mutation, cache ingestion or store initialization occurs.

Bounds: 2 MiB PGN, 10,000 mainline plies, 64 variation nesting levels and 100,000
lexical tokens; engine enrichment remains unsupported for games over 1,000 plies
or unknown roots. Selected-board move lists cap at 256; opportunity probes at 16;
child/PV pushes at 512; output at 32 findings, eight explanations and 256 KiB.
Time checks are cooperative (one second board work / two seconds including optional
evidence), not hard process preemption. Partial coverage and omissions are explicit;
absence of a finding never establishes tactical safety. There is no persistent
understanding cache. Native dispatch remains default-disabled under the unchanged
512 MiB policy.

The existing `CHESS_COACH_READ_ONLY=true` deployment boundary permits this GET
without expanding its mutation allowlist. Legacy stored JSON, CPL, critical-move,
accuracy, coaching/profile calculations and engine execution code are unchanged.
Use temporary databases for tests. Frontend helper checks include
`node --test tests/*.mjs` from `web/`. All implementation changes are left uncommitted;
the user handles Git and GitHub actions manually.

### Personal puzzle practice (Segment 20)

`/practice` offers explicit, bounded discovery from up to three selected games and
an exact player/platform identity. Mate-in-one objectives are graded by legal
replay and checkmate detection for **every** submitted move. Guided exercises
show legal tactical witnesses; other legal choices remain unassessed, never
labeled objectively wrong. No engine, cloud, provider or analysis job is started.

Initialize a **separate** local store explicitly (use canonical absolute paths;
never point it at a library, cache, progressive store or backup):

```sh
.venv/bin/python -m src.practice_cli init --db /absolute/path/practice.sqlite3 --library /absolute/path/library.db
```

Set `CHESS_COACH_PRACTICE_DB` to that file and set
`CHESS_COACH_PRACTICE_WRITES=true` to enable generation and practice events.
`CHESS_COACH_READ_ONLY=true` continues to block all practice writes. Initialization
does not open or migrate the library. Bind the API to loopback; browser writes
accept only the existing localhost/127.0.0.1 port-3000 frontend origins and the
practice JSON action header. This is single-user local software, not remote
multi-user authentication. The UI uses `NEXT_PUBLIC_API_URL` as usual.

Attempts, hints, reveals, retries and idempotent revision-fenced events survive
restart. An attempt URL contains its opaque ID for reload/resume. Comment edits
or deleted sources conservatively block continued grading. Opening the original
game can reveal the continuation. Questions omit private witnesses; the owner
can still inspect their local practice database.

Default capacities are 5,000 puzzles, 50,000 attempts, 500,000 events and 512 MiB
of logical serialized payload, with a 64-event limit per attempt. Optional
`CHESS_COACH_PRACTICE_LIMITS` JSON must match the limits used when initializing the
store. Capacity refusal never deletes history. SQLite indexes/pages/journals are
additional disk space, so this is not a physical-file quota. The CLI currently
initializes with defaults; custom limits require explicit programmatic init.
All personal SQLite files and sidecars remain Git-ignored.

Discovery is sampled (12 roots maximum), not a tactical scan of entire games.
No verified-blunder, best-move, numerical difficulty or player-strength claim is
made. Engine-evidence enrichment, multi-move grading, adaptive scheduling and
spaced repetition are deferred. Native progressive dispatch and its 512 MiB RSS
policy are unchanged.

### Selected-position reasoning (Segment 20.5)

Both game-review modes have a separate, explicitly activated **Reasoning** panel.
It compares compatible recorded root-MultiPV or sibling-child cohorts, displays
legal opponent continuations and checkpoint facts, and separates searched
observations from heuristic interpretations. Missing alternatives stay
unassessed; no global best-move, forced-win or causal attribution is inferred.

This release never launches an engine or provider. Import an **already available**
independent export through the offline `src.reasoning_import` CLI; point
`CHESS_COACH_REASONING_DIR` at its dedicated accepted-artifact directory. GET-only
routes work with `CHESS_COACH_READ_ONLY=true` and never initialize stores. The
existing cache is not sufficient producer-lineage proof, and native progressive
search remains disabled/unqualified under the unchanged 512 MiB policy.

See [the format and explicitly synthetic example](examples/reasoning_format.md).
Operator acceptance is a provenance acknowledgement, not cryptographic proof of
engine execution. Synthetic demo comparisons require the separate explicit
`CHESS_COACH_REASONING_ALLOW_SYNTHETIC=true` flag and remain visibly synthetic.
The frozen 14-case contract corpus does **not** establish chess-quality accuracy;
that evaluation remains NOT_RUN without independently reviewed genuine evidence.
No puzzle grading, legacy analysis, CPL or coaching calculation is changed.
