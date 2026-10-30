"""Disposable generated keys only; no real app store, tokens or network."""
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from investment import access


class AccessKeyTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / "work" / "access-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.scratch = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.scratch.name).resolve()
        self.directory = self.root / "investment" / "work" / "private" / "access"

    def tearDown(self):
        self.scratch.cleanup()

    def assert_key_equal(self, first, second):
        # An assertion failure must not echo an access credential.
        self.assertTrue(hmac.compare_digest(first, second), "active key mismatch")

    def test_first_create_then_load_preserves_one_stable_key(self):
        first = access.load_or_create_access_key(self.root)
        self.assertTrue(bool(access.KEY_PATTERN.fullmatch(first)))
        self.assertGreaterEqual(len(first), 48)
        self.assert_key_equal(first, access.load_or_create_access_key(self.root))
        self.assertEqual(1, len(list(self.directory.glob("key-*.token"))))
        self.assertTrue((self.directory / "current.json").is_file())
        self.assertFalse((self.directory / "access.lock").exists())

    def test_restart_uses_persisted_key_without_generator(self):
        first = access.load_or_create_access_key(self.root)
        with patch.object(access.secrets, "token_urlsafe", side_effect=AssertionError("must not create new key")):
            self.assert_key_equal(first, access.load_or_create_access_key(self.root))

    def test_rotation_preserves_old_immutable_files_and_switches_current(self):
        first = access.load_or_create_access_key(self.root)
        first_version = next(self.directory.glob("key-*.token"))
        before = hashlib.sha256(first_version.read_bytes()).hexdigest()
        second = access.rotate_access_key(self.root)
        self.assertFalse(hmac.compare_digest(first, second))
        self.assertEqual(before, hashlib.sha256(first_version.read_bytes()).hexdigest())
        self.assertEqual(2, len(list(self.directory.glob("key-*.token"))))
        self.assert_key_equal(second, access.load_or_create_access_key(self.root))
        pointer = json.loads((self.directory / "current.json").read_text())
        self.assertNotEqual(first_version.name, pointer["keyFile"])

    def test_concurrent_initializations_all_get_one_key(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            values = list(executor.map(lambda _: access.load_or_create_access_key(self.root), range(12)))
        self.assertTrue(all(hmac.compare_digest(values[0], value) for value in values))
        self.assertEqual(1, len(list(self.directory.glob("key-*.token"))))

    def test_simultaneous_rotations_leave_valid_current_and_every_version(self):
        access.load_or_create_access_key(self.root)
        with ThreadPoolExecutor(max_workers=4) as executor:
            values = list(executor.map(lambda _: access.rotate_access_key(self.root), range(4)))
        current = access.load_or_create_access_key(self.root)
        self.assertTrue(any(hmac.compare_digest(current, value) for value in values))
        self.assertEqual(5, len(list(self.directory.glob("key-*.token"))))

    def test_unknown_content_is_not_erased_or_silently_reinitialized(self):
        self.directory.mkdir(parents=True)
        unknown = self.directory / "unexpected.txt"
        unknown.write_text("nonsecret sentinel")
        with self.assertRaisesRegex(access.AccessError, "ACCESS_STORE_UNKNOWN_CONTENT"):
            access.load_or_create_access_key(self.root)
        self.assertEqual("nonsecret sentinel", unknown.read_text())
        self.assertFalse((self.directory / "current.json").exists())

    def test_empty_corrupt_pointer_fails_closed_and_rotation_does_not_bypass_it(self):
        access.load_or_create_access_key(self.root)
        pointer = self.directory / "current.json"
        pointer.write_text("")
        for operation in (access.load_or_create_access_key, access.rotate_access_key):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(access.AccessError, "ACCESS_POINTER_INVALID"):
                    operation(self.root)
        self.assertEqual(1, len(list(self.directory.glob("key-*.token"))))
        self.assertEqual("", pointer.read_text())

    def test_missing_pointer_with_old_keys_is_not_reinitialized(self):
        access.load_or_create_access_key(self.root)
        (self.directory / "current.json").unlink()
        with self.assertRaisesRegex(access.AccessError, "ACCESS_POINTER_MISSING"):
            access.load_or_create_access_key(self.root)
        self.assertEqual(1, len(list(self.directory.glob("key-*.token"))))

    def test_key_corruption_and_digest_mismatch_are_rejected_without_secret_errors(self):
        first = access.load_or_create_access_key(self.root)
        key_file = next(self.directory.glob("key-*.token"))
        key_file.write_text("short")
        with self.assertRaisesRegex(access.AccessError, "ACCESS_KEY_INVALID"):
            access.load_or_create_access_key(self.root)
        key_file.write_text("A" * 64)
        with self.assertRaises(access.AccessError) as caught:
            access.load_or_create_access_key(self.root)
        self.assertEqual("ACCESS_KEY_DIGEST_MISMATCH", str(caught.exception))
        self.assertNotIn(first, str(caught.exception))

    def test_pointer_traversal_and_duplicate_fields_are_rejected(self):
        access.load_or_create_access_key(self.root)
        pointer = self.directory / "current.json"
        contents = json.loads(pointer.read_text())
        contents["keyFile"] = "../elsewhere.token"
        pointer.write_text(json.dumps(contents))
        with self.assertRaisesRegex(access.AccessError, "ACCESS_POINTER_INVALID"):
            access.load_or_create_access_key(self.root)
        pointer.write_text('{"format":"one","format":"two"}')
        with self.assertRaisesRegex(access.AccessError, "ACCESS_POINTER_INVALID"):
            access.load_or_create_access_key(self.root)

    def test_relative_unc_and_non_directory_roots_are_rejected(self):
        for root in ("relative-app-root", r"\\server\share\app", "//server/share/app"):
            with self.subTest(root=root):
                with self.assertRaises(access.AccessError):
                    access.load_or_create_access_key(root)
        ordinary_file = self.root / "ordinary.txt"
        ordinary_file.write_text("nonsecret sentinel")
        with self.assertRaisesRegex(access.AccessError, "ACCESS_ROOT_NOT_DIRECTORY"):
            access.load_or_create_access_key(ordinary_file)

    def test_existing_claim_is_never_deleted_by_another_caller(self):
        self.directory.mkdir(parents=True)
        claim = self.directory / "access.lock"
        claim.write_text("nonsecret owner marker")
        with patch.object(access, "LOCK_WAIT_SECONDS", 0):
            with self.assertRaisesRegex(access.AccessError, "ACCESS_STORE_BUSY_OR_INTERRUPTED"):
                access.load_or_create_access_key(self.root)
        self.assertEqual("nonsecret owner marker", claim.read_text())

    def test_windows_delete_pending_permission_error_retries_only_observed_lock(self):
        self.directory.mkdir(parents=True)
        claim = self.directory / "access.lock"
        claim.write_text("nonsecret other owner")
        original_open = access.os.open
        count = 0

        def pending_then_open(path, flags, *args, **kwargs):
            nonlocal count
            if Path(path) == claim:
                count += 1
                if count == 1:
                    claim.unlink()  # Previous owner removed the observed claim.
                    raise PermissionError(errno.EACCES, "fictional delete-pending claim")
            return original_open(path, flags, *args, **kwargs)

        with patch.object(access.os, "open", side_effect=pending_then_open):
            active = access.load_or_create_access_key(self.root)
        self.assertGreaterEqual(count, 2)
        self.assertTrue(bool(access.KEY_PATTERN.fullmatch(active)))

    def test_unexplained_lock_permission_denial_is_not_retried(self):
        self.directory.mkdir(parents=True)
        with patch.object(access.os, "open", side_effect=PermissionError(errno.EACCES, "fictional real denial")) as opening, \
                patch.object(access.time, "sleep") as sleeping:
            with self.assertRaisesRegex(access.AccessError, "ACCESS_STORE_UNAVAILABLE"):
                access.load_or_create_access_key(self.root)
        self.assertEqual(1, opening.call_count)
        sleeping.assert_not_called()
        self.assertFalse((self.directory / "current.json").exists())

    def test_observed_lock_permission_contention_has_bounded_timeout(self):
        self.directory.mkdir(parents=True)
        claim = self.directory / "access.lock"
        claim.write_text("nonsecret other owner")
        with patch.object(access, "LOCK_WAIT_SECONDS", 0), \
                patch.object(access.os, "open", side_effect=PermissionError(errno.EACCES, "fictional contention")) as opening:
            with self.assertRaisesRegex(access.AccessError, "ACCESS_STORE_BUSY_OR_INTERRUPTED"):
                access.load_or_create_access_key(self.root)
        self.assertEqual(1, opening.call_count)
        self.assertEqual("nonsecret other owner", claim.read_text())

    def test_unexpected_lock_type_is_not_retried_as_permission_contention(self):
        self.directory.mkdir(parents=True)
        (self.directory / "access.lock").mkdir()
        with patch.object(access.os, "open") as opening:
            with self.assertRaisesRegex(access.AccessError, "ACCESS_STORE_FILE_INVALID"):
                access.load_or_create_access_key(self.root)
        opening.assert_not_called()

    def test_failed_atomic_pointer_replace_preserves_old_active_key(self):
        first = access.load_or_create_access_key(self.root)
        pointer_before = (self.directory / "current.json").read_bytes()
        with patch.object(access.os, "replace", side_effect=OSError("fixed nonsecret failure")):
            with self.assertRaisesRegex(access.AccessError, "ACCESS_STORE_UNAVAILABLE"):
                access.rotate_access_key(self.root)
        self.assertEqual(pointer_before, (self.directory / "current.json").read_bytes())
        self.assertEqual(2, len(list(self.directory.glob("key-*.token"))))
        # Interrupted pending files are investigated, never silently reset.
        with self.assertRaisesRegex(access.AccessError, "ACCESS_STORE_UNKNOWN_CONTENT"):
            access.load_or_create_access_key(self.root)
        self.assertTrue(bool(access.KEY_PATTERN.fullmatch(first)))

    def test_directory_symlink_is_rejected_when_os_permits_test_creation(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.root / "investment"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit disposable symlink creation")
        with self.assertRaisesRegex(access.AccessError, "ACCESS_PATH_REPARSE_REJECTED"):
            access.load_or_create_access_key(self.root)
        self.assertEqual([], list(outside.iterdir()))

    def test_windows_reparse_attribute_is_rejected_without_following_target(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with patch.object(access.Path, "lstat", return_value=info):
            with self.assertRaisesRegex(access.AccessError, "ACCESS_PATH_REPARSE_REJECTED"):
                access.load_or_create_access_key(self.root)

    def test_hardlinked_key_is_not_treated_as_private_single_file(self):
        access.load_or_create_access_key(self.root)
        key_file = next(self.directory.glob("key-*.token"))
        alias = self.root / "external-hardlink.token"
        try:
            os.link(key_file, alias)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit disposable hardlink creation")
        with self.assertRaisesRegex(access.AccessError, "ACCESS_STORE_FILE_INVALID"):
            access.load_or_create_access_key(self.root)


if __name__ == "__main__":
    unittest.main()
