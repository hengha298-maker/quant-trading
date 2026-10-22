"""Conservative filename guard; manual review remains necessary.

Git repositories retain staged/tracked checks. A source ZIP, or a machine
without Git, uses a separately labelled full filesystem scan. That scan ignores
only the root .git metadata, not .gitignore, work, virtualenvs, or caches. It does
not read file contents and cannot identify renamed or embedded private data.
"""
import os
from pathlib import Path
import stat
import subprocess
import sys
REPO = Path(__file__).resolve().parents[1]
ALLOW = {'config.example.json', 'bark.example.json'}
FORBIDDEN = {'.csv', '.json', '.html', '.db', '.sqlite', '.sqlite3', '.zip', '.bak', '.xlsx', '.xls', '.log'}


def _forbidden(name):
    path = Path(name)
    return name not in ALLOW and (path.suffix.lower() in FORBIDDEN or any(
        part.lower() in ('snapshots', 'reports', 'private', 'backups') for part in path.parts))


def _link_or_reparse(info):
    # Junctions and other Windows reparse points must not be traversed either.
    return (stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_reparse_tag', 0))
            or bool(getattr(info, 'st_file_attributes', 0) & 0x400))


def _filesystem_files():
    """Enumerate names without following links, failing on incomplete scans."""
    root_info = REPO.lstat()
    if _link_or_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError('unsafe scan root')
    pending = [REPO]
    names = []
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                # Git metadata is not a source artifact; never traverse it.
                if directory == REPO and entry.name == '.git':
                    continue
                path = Path(entry.path)
                # DirEntry.stat can report zero inode/device IDs on Windows;
                # lstat obtains the comparable metadata without following links.
                info = path.lstat()
                if _link_or_reparse(info):
                    raise ValueError('linked scan entry')
                name = path.relative_to(REPO).as_posix()
                names.append(name)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(info.st_mode):
                    # Verify access, but never read or print file contents.
                    # O_NOFOLLOW closes the final-component link race where
                    # supported; Windows reparse points were rejected above.
                    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                    try:
                        opened = os.fstat(descriptor)
                        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError('scan entry changed')
                    finally:
                        os.close(descriptor)
                else:
                    raise ValueError('unknown scan entry type')
    return names


def _filesystem_check():
    print('检查方式：文件系统全量扫描（非Git暂存区检查）；仅排除根目录.git元数据，不按.gitignore跳过目录。')
    try:
        files = _filesystem_files()
    except (OSError, ValueError, RuntimeError):
        print('拒绝：无法完成文件系统全量扫描；可能有无权限、未知类型或链接，不能视为检查通过。')
        return 1
    if any(_forbidden(name) for name in files):
        print('拒绝：发现可能包含私有数据的文件；禁止提交 CSV、快照、报告、数据库或备份。')
        return 1
    print('数据文件检查通过（文件系统全量扫描，非Git暂存区检查）；仍须人工审阅源码，无法识别所有改名或嵌入源码的隐私。')
    return 0


def check(all_files=False):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # A ZIP unpacked inside an unrelated parent repository is still a ZIP.
    # Checking this exact source root avoids silently using the parent's index.
    try:
        (REPO / '.git').lstat()
    except FileNotFoundError:
        return _filesystem_check()
    except OSError:
        print('拒绝：无法确认源码根目录的Git元数据，不能完成检查。')
        return 1
    args = ['git', 'ls-files', '-z'] if all_files else ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']
    try:
        files = subprocess.check_output(args, cwd=REPO, stderr=subprocess.DEVNULL).decode('utf-8').split('\0')
    except FileNotFoundError:
        return _filesystem_check()
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        print('拒绝：Git检查失败；不能视为暂存区或跟踪文件检查通过。')
        return 1
    bad = [name for name in files if name and _forbidden(name)]
    if bad:
        print('拒绝：发现可能包含私有数据的文件；禁止提交 CSV、快照、报告、数据库或备份。')
        return 1
    print('数据文件检查通过；仍须人工审阅 diff，无法识别所有改名或嵌入源码的隐私。')
    return 0


if __name__ == '__main__':
    raise SystemExit(check('--all' in sys.argv))
