"""Offline reporting safeguards using fiction and workspace-owned temp files."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import cli
from goose_research.reports import read_v1_status


class ReportingEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cli.WORK.mkdir(exist_ok=True)
        cls.synthetic = cli.synthetic_dataset()

    def test_two_post_warmup_sessions_produce_explicit_blocked_output_not_type_error(self):
        dataset = deepcopy(self.synthetic)
        dataset["bars"] = dataset["bars"][:23]
        dataset["calendar"]["sessions"] = dataset["calendar"]["sessions"][:23]
        before = deepcopy(dataset)
        with tempfile.TemporaryDirectory(prefix="reporting-edge-", dir=cli.WORK) as temporary:
            output = Path(temporary)/"short-sample"
            result = cli.run_research(dataset, out=output,
                now=datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc))
            self.assertTrue(result["blocked"])
            blocked = json.loads((output/"blocked.json").read_text(encoding="utf-8"))
            self.assertEqual(blocked["reason"], "INSUFFICIENT_SESSIONS_FOR_THREE_RESEARCH_PARTITIONS")
            self.assertFalse(blocked["returnsPublished"])
            self.assertTrue(blocked["calculationCompleted"])
            self.assertFalse((output/"backtest.json").exists())
            self.assertFalse((output/"goose-report-v32.json").exists())
            report = json.loads((output/"goose-report-v31.json").read_text(encoding="utf-8"))
            self.assertIsNone(report["backtest"])
            self.assertEqual(report["paperTrades"], [])
            self.assertEqual(report["signals"][0]["stance"], "watch")
            self.assertTrue((output/"run.json").is_file())
        self.assertEqual(dataset, before)

    def test_corrupt_sqlite_is_a_fixed_value_error_without_raw_data_or_driver_details(self):
        with tempfile.TemporaryDirectory(prefix="reporting-edge-", dir=cli.WORK) as temporary:
            path = Path(temporary)/"fictional-corrupt.sqlite3"
            contents = b"NOT_A_SQLITE_DATABASE_FICTIONAL_SECRET_ONLY"
            path.write_bytes(contents)
            with self.assertRaises(ValueError) as failure:
                read_v1_status(path)
            self.assertEqual(str(failure.exception), "V1_SQLITE_READ_FAILED")
            self.assertNotIn("FICTIONAL_SECRET", str(failure.exception))
            self.assertEqual(path.read_bytes(), contents)
            self.assertFalse(any(Path(str(path)+suffix).exists() for suffix in ("-wal", "-shm", "-journal")))

    def test_wal_created_during_select_is_rejected_even_when_main_database_hash_is_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="reporting-edge-", dir=cli.WORK) as temporary:
            path = Path(temporary)/"fictional-status.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("CREATE TABLE events(ts TEXT);INSERT INTO events VALUES('2025-01-02T14:50:00+08:00');")
            connection.commit(); connection.close()
            digest = sha256(path.read_bytes()).hexdigest()
            wal = Path(str(path)+"-wal")
            real_connect = sqlite3.connect
            injected = []

            class ConcurrentWalConnection:
                def __init__(self, *arguments, **keywords):
                    self.connection = real_connect(*arguments, **keywords)

                def execute(self, sql, *arguments):
                    cursor = self.connection.execute(sql, *arguments)
                    if sql.startswith("SELECT MAX(") and not injected:
                        # A synthetic marker represents a concurrent WAL writer.
                        # It appears only after the preflight checks, and the
                        # immutable reader does not modify the actual database.
                        wal.write_bytes(b"FICTIONAL_CONCURRENT_WAL_MARKER")
                        injected.append(True)
                    return cursor

                def close(self):
                    self.connection.close()

            with patch("goose_research.reports.sqlite3.connect", side_effect=ConcurrentWalConnection):
                with self.assertRaises(ValueError) as failure:
                    read_v1_status(path)
            self.assertEqual(str(failure.exception), "V1_SQLITE_BUSY_OR_JOURNAL_PRESENT")
            self.assertEqual(injected, [True])
            self.assertTrue(wal.exists())
            self.assertEqual(sha256(path.read_bytes()).hexdigest(), digest)

    def test_publish_rejects_reparse_web_or_ancestors_before_replacing_any_report(self):
        with tempfile.TemporaryDirectory(prefix="reporting-edge-", dir=cli.WORK) as temporary:
            workspace = Path(temporary)/"fictional-V3.2-dev"
            root = workspace/"v32"
            root.mkdir(parents=True)
            web = workspace/"v3"/"web"
            web.mkdir(parents=True)
            (web/"index.html").write_text("V3.2 fictional test shell", encoding="utf-8")
            folder = Path(temporary)/"fictional-source-reports"
            folder.mkdir()
            for version in ("v31", "v32"):
                (folder/("goose-report-"+version+".json")).write_text('{"fictional":true}', encoding="utf-8")
                (web/("research-report-"+version+".json")).write_bytes(b"FICTIONAL_EXISTING_REPORT")
            original_is_symlink = Path.is_symlink
            for reparse in (web, web.parent, workspace):
                with self.subTest(reparse=reparse.name):
                    def simulated_symlink(path):
                        return path == reparse or original_is_symlink(path)
                    with patch.object(cli, "ROOT", root), patch.object(Path, "is_symlink", simulated_symlink):
                        with self.assertRaisesRegex(ValueError, "^PUBLISH_REPARSE_REJECTED$"):
                            cli.publish_reports(folder)
                    for version in ("v31", "v32"):
                        self.assertEqual((web/("research-report-"+version+".json")).read_bytes(), b"FICTIONAL_EXISTING_REPORT")
                    self.assertEqual(list(web.glob("*.pending")), [])


if __name__ == "__main__":
    unittest.main()
