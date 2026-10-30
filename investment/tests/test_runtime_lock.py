from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from investment.runtime_lock import analysis_mutex, BUSY_CODE, mutex_name

TEST_ROOT = ROOT / "investment" / "work" / "runtime-lock-unit-tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)
CHILD = """
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from investment.runtime_lock import analysis_mutex
with analysis_mutex(sys.argv[2]):
    Path(sys.argv[3]).write_text('OWNED_BY_FICTIONAL_TEST_CHILD', encoding='ascii')
    sys.stdin.buffer.read(1)
"""


class RuntimeLockTests(unittest.TestCase):
    def test_normal_release_and_exception_release(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as root:
            with analysis_mutex(root):
                pass
            with self.assertRaisesRegex(RuntimeError, "FICTIONAL"):
                with analysis_mutex(root):
                    raise RuntimeError("FICTIONAL")
            with analysis_mutex(root):
                pass
            self.assertFalse((Path(root)/".analysis.lock").exists())

    def test_legacy_lock_is_preserved_and_blocks(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as root:
            legacy = Path(root)/".analysis.lock"
            legacy.write_bytes(b"DO_NOT_DELETE_LEGACY_LOCK")
            with self.assertRaisesRegex(ValueError, BUSY_CODE):
                with analysis_mutex(root):
                    self.fail("An old worker might still be active")
            self.assertEqual(legacy.read_bytes(), b"DO_NOT_DELETE_LEGACY_LOCK")

    def _child(self, root):
        ready = Path(root)/"fictional-child-ready.txt"
        child = subprocess.Popen([sys.executable, "-I", "-B", "-c", CHILD,
            str(ROOT), str(root), str(ready)], stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        limit = time.monotonic()+12
        while not ready.exists():
            if child.poll() is not None:
                error = child.communicate(timeout=2)[1]
                self.fail("Fictional lock worker exited: " + error.decode("utf8", errors="replace")[:300])
            if time.monotonic() > limit:
                child.kill()
                child.communicate(timeout=3)
                self.fail("Fictional lock worker did not become ready")
            time.sleep(.02)
        return child

    def _finish_child(self, child, *, terminate=False):
        # This test owns precisely this Popen child, never a discovered process.
        if child.poll() is None:
            if terminate:
                child.kill()
                child.communicate(timeout=5)
            else:
                child.communicate(input=b"x", timeout=5)

    def test_cross_process_exclusivity_then_normal_exit(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as root:
            child = self._child(root)
            try:
                with self.assertRaisesRegex(ValueError, BUSY_CODE):
                    with analysis_mutex(root):
                        self.fail("Two process owners")
                self._finish_child(child)
                with analysis_mutex(root):
                    pass
            finally:
                self._finish_child(child, terminate=True)

    def test_killed_fictional_child_recovers_without_deleting_files(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as root:
            child = self._child(root)
            keeper = None
            api = None
            try:
                if os.name == "nt":
                    # Retain a nonowning handle so the same abandoned mutex
                    # object survives child death until the next acquisition.
                    from investment.runtime_lock import _windows_api
                    api = _windows_api()
                    keeper = api.CreateMutexW(None, False, mutex_name(root))
                    self.assertTrue(keeper)
                self._finish_child(child, terminate=True)
                with analysis_mutex(root):
                    pass
                self.assertFalse((Path(root)/".analysis.lock").exists())
                self.assertTrue((Path(root)/"fictional-child-ready.txt").exists())
            finally:
                if keeper:
                    api.CloseHandle(keeper)
                self._finish_child(child, terminate=True)


if __name__ == "__main__":
    unittest.main()
