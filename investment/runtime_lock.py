"""Process-lifetime analysis exclusion; interrupted workers never leave a lock.

This changes neither Windows services nor the registry. Legacy empty-file locks
are deliberately left alone: an older worker might still own their operation.
Daily generation/delivery claims are separate and remain fail-closed.
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import os
from pathlib import Path

BUSY_CODE = "ANALYSIS_ALREADY_RUNNING_OR_INTERRUPTED_CHECK_LOCK"
UNAVAILABLE_CODE = "ANALYSIS_LOCK_UNAVAILABLE"


def _directory(workpath):
    path = Path(os.path.abspath(workpath))
    for parent in (path, *path.parents):
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError(UNAVAILABLE_CODE)
    if not path.is_dir():
        raise ValueError(UNAVAILABLE_CODE)
    return path


def _legacy_guard(path):
    if os.path.lexists(path / ".analysis.lock"):
        raise ValueError(BUSY_CODE)


def mutex_name(workpath):
    key = os.path.normcase(os.path.abspath(workpath)).encode("utf8")
    return "Local\\GooseInvestmentAnalysis-" + sha256(key).hexdigest()


def _windows_api():
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = [wintypes.HANDLE]
    api.ReleaseMutex.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


@contextmanager
def analysis_mutex(workpath):
    """Acquire immediately or raise a fixed error; never delete lock files.

    Windows abandons a held kernel mutex when its owner exits or is killed.
    WAIT_ABANDONED therefore grants ownership just like WAIT_OBJECT_0. Reports
    are immutable and the generator creates a fresh run directory after either
    acquisition; no incomplete old report is resumed.
    """
    path = _directory(workpath)
    _legacy_guard(path)
    if os.name == "nt":
        api = _windows_api()
        handle = api.CreateMutexW(None, False, mutex_name(path))
        if not handle:
            raise ValueError(UNAVAILABLE_CODE)
        owned = False
        try:
            result = api.WaitForSingleObject(handle, 0)
            if result == 0x102:  # WAIT_TIMEOUT, another thread/process owns it.
                raise ValueError(BUSY_CODE)
            if result not in (0, 0x80):  # WAIT_OBJECT_0 / WAIT_ABANDONED
                raise ValueError(UNAVAILABLE_CODE)
            owned = True
            _legacy_guard(path)
            yield
        finally:
            try:
                if owned:
                    api.ReleaseMutex(handle)
            finally:
                api.CloseHandle(handle)
    else:
        import errno
        import fcntl
        import stat
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path / ".analysis-owner.lock", flags, 0o600)
        except OSError:
            raise ValueError(UNAVAILABLE_CODE) from None
        locked = False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError(UNAVAILABLE_CODE)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise ValueError(BUSY_CODE) from None
                raise ValueError(UNAVAILABLE_CODE) from None
            _legacy_guard(path)
            yield
        finally:
            try:
                if locked:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
