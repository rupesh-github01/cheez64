"""
Tests for Segment 11A: Bulk Historical Game Import.

Covers:
- Multi-batch import from Chess.com and Lichess
- Resumable import with checkpointing after interruption
- Idempotent repeated imports and duplicate detection
- HTTP 429 and transient error retry with backoff
- Permanent error fast-fail (no retry)
- Preservation of existing analyses and NOT_ANALYZED status for new games
- Idempotent database schema migration (import_jobs table)
- Dry-run import mode
- Malformed/invalid PGN handling within batches
"""

import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure repo root is on sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import (
    GameLibrary,
    ANALYSIS_NOT_ANALYZED,
    ANALYSIS_ANALYZED,
    GameAnalysisRecord,
)
from src.importers import (
    ChessComImporter,
    LichessImporter,
    ImportBatch,
    ImportResult,
    bulk_import_platform_games,
    RateLimitError,
    UserNotFoundError,
    NetworkError,
    APIError,
)


def _make_sample_pgn(white: str, black: str, date_str: str, site_id: str, moves: str = "1. e4 e5 2. Nf3 Nc6") -> str:
    return f"""[Event "Bulk Test Game"]
[Site "https://example.com/{site_id}"]
[Date "{date_str}"]
[White "{white}"]
[Black "{black}"]
[Result "1-0"]
[WhiteElo "1600"]
[BlackElo "1550"]

{moves} 1-0
"""


class TestBulkImport(unittest.TestCase):
    """Test suite for bulk import functionality."""

    def setUp(self):
        self.lib = GameLibrary(":memory:")

    def tearDown(self):
        self.lib.close()

    # 1. Multi-batch Chess.com import
    @patch.object(ChessComImporter, "_make_request")
    def test_chesscom_bulk_multibatch(self, mock_req):
        archives_json = json.dumps({
            "archives": [
                "https://api.chess.com/pub/player/bob/games/2026/08",
                "https://api.chess.com/pub/player/bob/games/2026/09",
            ]
        }).encode("utf-8")

        aug_json = json.dumps({
            "games": [
                {
                    "url": "https://www.chess.com/game/live/8001",
                    "pgn": _make_sample_pgn("bob", "alice", "2026.08.10", "8001"),
                    "end_time": 1786320000,
                }
            ]
        }).encode("utf-8")

        sep_json = json.dumps({
            "games": [
                {
                    "url": "https://www.chess.com/game/live/9001",
                    "pgn": _make_sample_pgn("charlie", "bob", "2026.09.12", "9001"),
                    "end_time": 1789171200,
                }
            ]
        }).encode("utf-8")

        def fake_request(url, headers=None, method="GET"):
            if "archives" in url:
                return archives_json
            elif "2026/08" in url:
                return aug_json
            elif "2026/09" in url:
                return sep_json
            raise ValueError(f"Unexpected url: {url}")

        mock_req.side_effect = fake_request

        batches_seen = []
        def on_progress(p):
            batches_seen.append(p)

        importer = ChessComImporter(reverse_chronological=False)
        result = importer.bulk_import_games(
            "bob",
            self.lib,
            progress_callback=on_progress,
        )

        self.assertEqual(result.games_imported, 2)
        self.assertEqual(result.duplicates_skipped, 0)
        self.assertEqual(len(batches_seen), 2)

        # Check job in database
        jobs = self.lib.find_resumable_import_job("chess.com", "bob")
        # Since it completed successfully, it shouldn't be resumable
        self.assertIsNone(jobs)

        # Verify all imported games have status NOT_ANALYZED (Safeguard #6)
        games = self.lib.list_games()
        self.assertEqual(len(games), 2)
        for g in games:
            self.assertEqual(g.analysis_status, ANALYSIS_NOT_ANALYZED)

    # 2. Multi-batch Lichess streaming import
    @patch.object(LichessImporter, "_open_stream")
    def test_lichess_bulk_streaming_batches(self, mock_stream):
        # Generate 4 games
        pgn_stream_data = ""
        for i in range(1, 5):
            pgn_stream_data += _make_sample_pgn(
                f"user_{i}", "opponent", f"2026.10.0{i}", f"lich_{i}",
                moves=f"1. e4 e5 2. Nf3 Nc6 {i}. a3 a6"
            ) + "\n\n"

        # Mock streaming response
        mock_resp = io.BytesIO(pgn_stream_data.encode("utf-8"))
        mock_stream.return_value = mock_resp

        batches_seen = []
        def on_progress(p):
            batches_seen.append(p)

        importer = LichessImporter(batch_size=2)
        result = importer.bulk_import_games("myuser", self.lib, progress_callback=on_progress)

        self.assertEqual(result.games_imported, 4)
        self.assertEqual(result.duplicates_skipped, 0)
        # With batch_size=2 and 4 games, should have 2 batches
        self.assertEqual(len(batches_seen), 2)

        games = self.lib.list_games()
        self.assertEqual(len(games), 4)

    # 3. Resume after interruption
    @patch.object(ChessComImporter, "_make_request")
    def test_chesscom_resume_after_failure(self, mock_req):
        archives_json = json.dumps({
            "archives": [
                "https://api.chess.com/pub/player/carlsen/games/2026/01",
                "https://api.chess.com/pub/player/carlsen/games/2026/02",
                "https://api.chess.com/pub/player/carlsen/games/2026/03",
            ]
        }).encode("utf-8")

        g1_json = json.dumps({
            "games": [{
                "url": "https://www.chess.com/game/live/101",
                "pgn": _make_sample_pgn("carlsen", "opp1", "2026.01.05", "101"),
                "end_time": 1767571200,
            }]
        }).encode("utf-8")

        g2_json = json.dumps({
            "games": [{
                "url": "https://www.chess.com/game/live/102",
                "pgn": _make_sample_pgn("carlsen", "opp2", "2026.02.05", "102"),
                "end_time": 1770249600,
            }]
        }).encode("utf-8")

        g3_json = json.dumps({
            "games": [{
                "url": "https://www.chess.com/game/live/103",
                "pgn": _make_sample_pgn("carlsen", "opp3", "2026.03.05", "103"),
                "end_time": 1772668800,
            }]
        }).encode("utf-8")

        # First run: fail on month 2
        def run1_request(url, headers=None, method="GET"):
            if "archives" in url:
                return archives_json
            elif "2026/01" in url:
                return g1_json
            elif "2026/02" in url:
                raise NetworkError("Connection reset by peer")
            raise ValueError(f"Unexpected {url}")

        mock_req.side_effect = run1_request
        importer = ChessComImporter(reverse_chronological=False)
        result1 = importer.bulk_import_games("carlsen", self.lib)

        # 1 game was imported in batch 1 before failure
        self.assertEqual(result1.games_imported, 1)
        self.assertTrue(len(result1.errors) > 0)

        # Resumable job must exist with checkpoint = 2026/01
        resumable = self.lib.find_resumable_import_job("chess.com", "carlsen")
        self.assertIsNotNone(resumable)
        self.assertEqual(resumable["last_checkpoint"], "https://api.chess.com/pub/player/carlsen/games/2026/01")
        self.assertEqual(resumable["status"], "failed")

        # Second run: month 2 succeeds, month 3 succeeds
        def run2_request(url, headers=None, method="GET"):
            if "archives" in url:
                return archives_json
            elif "2026/01" in url:
                self.fail("Should not re-request month 01 because of checkpoint!")
            elif "2026/02" in url:
                return g2_json
            elif "2026/03" in url:
                return g3_json
            raise ValueError(f"Unexpected {url}")

        mock_req.side_effect = run2_request
        result2 = importer.bulk_import_games("carlsen", self.lib)

        # Second run imports remaining games (cumulative games_imported across job is 3)
        self.assertEqual(result2.games_imported, 3)
        self.assertEqual(len(result2.errors), 0)

        # Total 3 games now in library
        self.assertEqual(len(self.lib.list_games()), 3)

        # Job is now completed
        self.assertIsNone(self.lib.find_resumable_import_job("chess.com", "carlsen"))

    # 4. Idempotency on repeated imports
    @patch.object(ChessComImporter, "_make_request")
    def test_idempotent_repeated_import(self, mock_req):
        archives_json = json.dumps({
            "archives": ["https://api.chess.com/pub/player/nakamura/games/2026/05"]
        }).encode("utf-8")

        game_json = json.dumps({
            "games": [{
                "url": "https://www.chess.com/game/live/501",
                "pgn": _make_sample_pgn("nakamura", "opp", "2026.05.01", "501"),
                "end_time": 1777593600,
            }]
        }).encode("utf-8")

        mock_req.side_effect = lambda url, headers=None, method="GET": (
            archives_json if "archives" in url else game_json
        )

        importer = ChessComImporter()
        # First import
        res1 = importer.bulk_import_games("nakamura", self.lib)
        self.assertEqual(res1.games_imported, 1)
        self.assertEqual(res1.duplicates_skipped, 0)

        # Second import (e.g. repeated sync)
        res2 = importer.bulk_import_games("nakamura", self.lib)
        self.assertEqual(res2.games_imported, 0)
        self.assertEqual(res2.duplicates_skipped, 1)

        # Library still has only 1 game
        self.assertEqual(len(self.lib.list_games()), 1)

    # 5. Retry on 429 and transient 5xx errors with backoff
    @patch("time.sleep")
    @patch.object(ChessComImporter, "_make_request")
    def test_retry_on_429_success(self, mock_req, mock_sleep):
        archives_json = json.dumps({
            "archives": ["https://api.chess.com/pub/player/retryuser/games/2026/01"]
        }).encode("utf-8")

        game_json = json.dumps({
            "games": [{
                "url": "https://www.chess.com/game/live/1",
                "pgn": _make_sample_pgn("retryuser", "opp", "2026.01.01", "1"),
                "end_time": 1767225600,
            }]
        }).encode("utf-8")

        attempts = 0
        def request_with_transient_429(url, headers=None, method="GET"):
            nonlocal attempts
            if "archives" in url:
                return archives_json
            attempts += 1
            if attempts <= 2:
                raise RateLimitError("Rate limited (HTTP 429)")
            return game_json

        mock_req.side_effect = request_with_transient_429
        importer = ChessComImporter()
        result = importer.bulk_import_games("retryuser", self.lib)

        self.assertEqual(result.games_imported, 1)
        self.assertEqual(len(result.errors), 0)
        # Verifies retries occurred
        self.assertEqual(attempts, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    # 6. Fast failure on non-retryable 404 (UserNotFoundError)
    @patch("time.sleep")
    @patch.object(ChessComImporter, "_make_request")
    def test_no_retry_on_404(self, mock_req, mock_sleep):
        mock_req.side_effect = UserNotFoundError("User not found (HTTP 404)")
        importer = ChessComImporter()

        result = importer.bulk_import_games("nonexistent_player", self.lib)
        self.assertEqual(result.games_imported, 0)
        self.assertTrue(len(result.errors) > 0)
        # time.sleep should NOT be called for 404
        mock_sleep.assert_not_called()

    # 7. Analysis preservation on duplicate import (Safeguard #6)
    def test_preserves_existing_analyses(self):
        pgn = _make_sample_pgn("hero", "villain", "2026.01.01", "preserve_test")
        game, is_new = self.lib.add_game(pgn, source="chess.com", source_game_id="preserve_test")
        self.assertTrue(is_new)

        # Attach analysis record and mark ANALYZED
        self.lib.attach_analysis(
            game.game_id,
            analysis_data={"summary": {"note": "hero played well"}},
            engine_depth=16,
        )

        # Re-import batch containing the exact same game
        results = self.lib.add_games_batch([(pgn, "chess.com", "preserve_test")])
        self.assertEqual(len(results), 1)
        g_reimported, is_reimported_new = results[0]

        self.assertFalse(is_reimported_new)
        # Analysis status must remain ANALYZED, NOT overwritten to NOT_ANALYZED
        fresh = self.lib.get_game(game.game_id)
        self.assertEqual(fresh.analysis_status, ANALYSIS_ANALYZED)
        stored_analysis = self.lib.get_analysis(game.game_id)
        self.assertIsNotNone(stored_analysis)
        self.assertEqual(stored_analysis.get("summary", {}).get("note"), "hero played well")

    # 8. Idempotent schema initialization (Safeguard #7)
    def test_idempotent_schema_init(self):
        # Adding a job, then calling _init_db again should NOT wipe the table
        job = self.lib.create_import_job("chess.com", "migration_test")
        self.assertIsNotNone(job)

        # Run _init_db again (idempotent migration)
        self.lib._init_db()

        fetched = self.lib.get_import_job(job["job_id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["username"], "migration_test")

    # 9. Dry-run mode
    @patch.object(ChessComImporter, "_make_request")
    def test_dry_run_mode(self, mock_req):
        archives_json = json.dumps({
            "archives": ["https://api.chess.com/pub/player/dryuser/games/2026/01"]
        }).encode("utf-8")

        game_json = json.dumps({
            "games": [{
                "url": "https://www.chess.com/game/live/dry1",
                "pgn": _make_sample_pgn("dryuser", "opp", "2026.01.01", "dry1"),
                "end_time": 1767225600,
            }]
        }).encode("utf-8")

        mock_req.side_effect = lambda url, headers=None, method="GET": (
            archives_json if "archives" in url else game_json
        )

        importer = ChessComImporter()
        result = importer.bulk_import_games("dryuser", self.lib, dry_run=True)

        self.assertEqual(result.games_found, 1)
        self.assertEqual(result.games_imported, 0)
        # No games in library
        self.assertEqual(len(self.lib.list_games()), 0)
        # No import job created in library
        self.assertIsNone(self.lib.find_resumable_import_job("chess.com", "dryuser"))

    # 10. Malformed/empty PGN in batch
    @patch.object(ChessComImporter, "_make_request")
    def test_malformed_pgn_in_batch(self, mock_req):
        archives_json = json.dumps({
            "archives": ["https://api.chess.com/pub/player/badpgn/games/2026/01"]
        }).encode("utf-8")

        game_json = json.dumps({
            "games": [
                {
                    "url": "https://www.chess.com/game/live/valid",
                    "pgn": _make_sample_pgn("badpgn", "opp", "2026.01.01", "valid"),
                    "end_time": 1767225600,
                },
                {
                    "url": "https://www.chess.com/game/live/empty",
                    "pgn": "   ",
                    "end_time": 1767225601,
                },
                {
                    "url": "https://www.chess.com/game/live/corrupt",
                    "pgn": "Not a chess game at all ::: ;;;",
                    "end_time": 1767225602,
                },
            ]
        }).encode("utf-8")

        mock_req.side_effect = lambda url, headers=None, method="GET": (
            archives_json if "archives" in url else game_json
        )

        importer = ChessComImporter()
        result = importer.bulk_import_games("badpgn", self.lib)

        # 1 valid game imported, 2 invalid games skipped with warnings
        self.assertEqual(result.games_imported, 1)
        self.assertEqual(result.invalid_games, 2)
        self.assertTrue(len(result.warnings) >= 2)
        self.assertEqual(len(self.lib.list_games()), 1)


if __name__ == "__main__":
    unittest.main()
