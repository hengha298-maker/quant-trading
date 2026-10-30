"""One persistent local bearer link, with explicit append-only key rotation.

Only the current pointer is replaced. Old key version files are retained for
audit, but are not accepted by the server after it adopts the newly active key.
No bearer value is logged or returned in diagnostic messages.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import uuid


KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{48,128}\Z")
VERSION_PATTERN = re.compile(r"key-[0-9a-f]{32}\.token\Z")
LOCK_WAIT_SECONDS = 5
STORE_FORMAT = "goose-investment-private-access"
_PROCESS_CLAIMS = {}
_PROCESS_CLAIMS_GUARD = threading.Lock()


class AccessError(ValueError):
    """Fixed diagnostic code without private paths, token contents or raw errors."""


def _fail(code):
    raise AccessError(code)


def _check_path(path):
    """Never traverse a symlink, junction or other Windows reparse point."""
    for ancestor in reversed((path, *path.parents)):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("ACCESS_PATH_REPARSE_REJECTED")


def _store(root):
    raw = os.fspath(root)
    if not isinstance(raw, str) or not os.path.isabs(raw) or raw.startswith(("\\\\", "//")):
        _fail("ACCESS_LOCAL_ABSOLUTE_ROOT_REQUIRED")
    directory = Path(os.path.abspath(raw))
    _check_path(directory)
    if not directory.is_dir():
        _fail("ACCESS_ROOT_NOT_DIRECTORY")
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel.GetDriveTypeW.restype = ctypes.c_uint
        if kernel.GetDriveTypeW(directory.anchor) != 3:  # DRIVE_FIXED only.
            _fail("ACCESS_FIXED_LOCAL_DISK_REQUIRED")
    for component in ("investment", "work", "private", "access"):
        directory = directory / component
        _check_path(directory)
        directory.mkdir(mode=0o700, exist_ok=True)
        _check_path(directory)
        if not directory.is_dir():
            _fail("ACCESS_STORE_NOT_DIRECTORY")
    return directory


def _same_file(before, after):
    return (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)


def _regular_info(path, max_size=2048):
    _check_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > max_size:
        _fail("ACCESS_STORE_FILE_INVALID")
    return info


def _read(path, max_size=2048):
    before = _regular_info(path, max_size)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not _same_file(before, opened):
            _fail("ACCESS_STORE_CHANGED")
        content = stream.read(max_size + 1)
        after = os.fstat(stream.fileno())
    if len(content) > max_size or opened.st_size != after.st_size or opened.st_mtime_ns != after.st_mtime_ns:
        _fail("ACCESS_STORE_CHANGED")
    return content


@contextmanager
def _claim(directory):
    """Concurrent cooperating callers share one initialization or rotation."""
    # NTFS can briefly report EACCES while a just-unlinked file is delete-pending.
    # Serialize our own threads before touching that exact filesystem claim;
    # the exclusive file below still protects separate application processes.
    identity = os.path.normcase(str(directory))
    with _PROCESS_CLAIMS_GUARD:
        process_claim = _PROCESS_CLAIMS.setdefault(identity, threading.Lock())
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    if not process_claim.acquire(timeout=LOCK_WAIT_SECONDS):
        _fail("ACCESS_STORE_BUSY_OR_INTERRUPTED")
    try:
        with _file_claim(directory, deadline):
            yield
    finally:
        process_claim.release()


@contextmanager
def _file_claim(directory, deadline):
    lock = directory / "access.lock"
    observed_ordinary_lock = False
    while True:
        _check_path(directory)
        try:
            _regular_info(lock, 128)
            observed_ordinary_lock = True
        except FileNotFoundError:
            pass
        try:
            descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            owned = os.fstat(descriptor)
            os.close(descriptor)
            break
        except FileExistsError:
            try:
                _regular_info(lock, 128)
                observed_ordinary_lock = True
            except FileNotFoundError:
                # The previous owner finished between exclusive open and stat.
                pass
        except OSError as error:
            if error.errno != errno.EACCES and getattr(error, "winerror", None) not in {5, 32, 33}:
                raise
            # Retry permission/sharing errors ONLY with evidence that this exact
            # ordinary claim exists or was just removed. Never treat a denied
            # directory, unexpected link, invalid file or arbitrary I/O as busy.
            try:
                _regular_info(lock, 128)
                observed_ordinary_lock = True
            except (FileNotFoundError, PermissionError):
                if not observed_ordinary_lock:
                    raise error
            if not observed_ordinary_lock:
                raise
        if time.monotonic() >= deadline:
            _fail("ACCESS_STORE_BUSY_OR_INTERRUPTED")
        time.sleep(0.025)
    try:
        yield
    finally:
        # Delete only the exact claim created here, never another caller's lock.
        present = _regular_info(lock, 128)
        if not _same_file(owned, present):
            _fail("ACCESS_STORE_CHANGED")
        lock.unlink()


def _token(raw):
    try:
        value = raw.decode("ascii")
    except UnicodeError:
        _fail("ACCESS_KEY_INVALID")
    if not KEY_PATTERN.fullmatch(value):
        _fail("ACCESS_KEY_INVALID")
    return value


def _json_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("ACCESS_POINTER_INVALID")
        result[key] = value
    return result


def _active(directory):
    names = []
    for entry in directory.iterdir():
        if entry.name == "access.lock":
            continue
        if entry.name != "current.json" and not VERSION_PATTERN.fullmatch(entry.name):
            _fail("ACCESS_STORE_UNKNOWN_CONTENT")
        _regular_info(entry)
        names.append(entry.name)
    if not names:
        return None
    if "current.json" not in names:
        _fail("ACCESS_POINTER_MISSING")
    # Even unused/old version files must be known, valid local token files.
    versions = {name: _token(_read(directory / name, 128))
                for name in names if VERSION_PATTERN.fullmatch(name)}
    try:
        pointer = json.loads(_read(directory / "current.json").decode("utf-8"), object_pairs_hook=_json_no_duplicates)
    except (UnicodeError, json.JSONDecodeError):
        _fail("ACCESS_POINTER_INVALID")
    if (type(pointer) is not dict or set(pointer) != {"format", "version", "keyFile", "keySha256"}
            or pointer.get("format") != STORE_FORMAT or type(pointer.get("version")) is not int
            or pointer["version"] != 1 or not isinstance(pointer.get("keyFile"), str)
            or not VERSION_PATTERN.fullmatch(pointer["keyFile"])
            or pointer["keyFile"] not in versions
            or not isinstance(pointer.get("keySha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", pointer["keySha256"])):
        _fail("ACCESS_POINTER_INVALID")
    key = versions[pointer["keyFile"]]
    if not hmac.compare_digest(hashlib.sha256(key.encode("ascii")).hexdigest(), pointer["keySha256"]):
        _fail("ACCESS_KEY_DIGEST_MISMATCH")
    return key


def _write_new(directory):
    key = secrets.token_urlsafe(48)  # 384 random bits; no user/account information.
    if not KEY_PATTERN.fullmatch(key):
        _fail("ACCESS_GENERATOR_INVALID")
    name = "key-" + uuid.uuid4().hex + ".token"
    _check_path(directory)
    descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(key.encode("ascii"))
        stream.flush()
        os.fsync(stream.fileno())
    pointer = {"format": STORE_FORMAT, "version": 1, "keyFile": name,
               "keySha256": hashlib.sha256(key.encode("ascii")).hexdigest()}
    pending = directory / ("current-" + uuid.uuid4().hex + ".pending")
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    _check_path(directory)
    current = directory / "current.json"
    if current.exists():
        _regular_info(current)
    os.replace(pending, current)  # Complete pointer changes in one atomic step.
    if not hmac.compare_digest(_active(directory), key):
        _fail("ACCESS_STORE_CHANGED")
    return key


def _operation(root, rotate):
    try:
        directory = _store(root)
        with _claim(directory):
            active = _active(directory)
            return _write_new(directory) if rotate or active is None else active
    except AccessError:
        raise
    except (OSError, TypeError, ValueError):
        # Never include a raw exception that could contain bearer data or paths.
        _fail("ACCESS_STORE_UNAVAILABLE")


def load_or_create_access_key(root):
    """Return the current key; only a completely empty valid store initializes."""
    return _operation(root, False)


def rotate_access_key(root):
    """Append a new key version and switch current; never remove prior versions."""
    return _operation(root, True)
