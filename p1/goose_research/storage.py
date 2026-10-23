import json
import os
from pathlib import Path, PureWindowsPath
from uuid import uuid4
from datetime import datetime, timezone
from .validation import validate_document

REPO = Path(__file__).resolve().parents[2]
BLOCKED = ('onedrive', 'dropbox', 'icloud', 'google drive', 'googledrive', 'box', 'synologydrive')


def _drive_type(anchor):
    """Unicode Win32 API; unknown/unavailable answers must never imply local."""
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('kernel32', use_last_error=True).GetDriveTypeW
    api.argtypes = [wintypes.LPCWSTR]
    api.restype = wintypes.UINT
    return api(anchor)


def _check_windows_path(path):
    p = PureWindowsPath(path)
    # Reject UNC, extended/device namespaces, drive-relative and unknown roots.
    if str(path).replace('/', '\\').startswith('\\\\') or not p.is_absolute() or len(p.drive) != 2 or p.drive[1] != ':':
        raise ValueError('只允许可确认的本地磁盘路径，禁止网络或设备路径')
    try:
        fixed = _drive_type(p.anchor) == 3  # DRIVE_FIXED only; not REMOTE/UNKNOWN
    except (OSError, AttributeError):
        raise ValueError('无法确认本地固定磁盘') from None
    if not fixed:
        raise ValueError('禁止网络驱动器或无法确认的存储')


def _resolve_target(path):
    # strict=False hides broken/inaccessible links. Resolve the deepest existing
    # ancestor strictly, then append only components known to be nonexistent.
    cursor = path
    missing = []
    while True:
        try:
            info = cursor.lstat()
            break
        except FileNotFoundError:
            if cursor == cursor.parent:
                raise ValueError('无法解析存储根目录') from None
            missing.append(cursor.name)
            cursor = cursor.parent
    target = cursor.resolve(strict=True)
    if os.name == 'nt':
        # Fail closed for unsupported reparse points (e.g. cloud placeholders).
        for component in (cursor, *cursor.parents, target, *target.parents):
            info = component.lstat()
            tag = getattr(info, 'st_reparse_tag', 0)
            if tag and tag not in (0xA000000C, 0xA0000003):  # symlink / junction
                raise ValueError('不支持的重解析存储目标')
    return target.joinpath(*reversed(missing))


def local_path(path):
    raw = os.fspath(path)
    if not isinstance(raw, str) or raw.replace('/', '\\').startswith('\\\\'):
        raise ValueError('禁止UNC或设备路径')
    if os.name != 'nt' and PureWindowsPath(raw).drive:
        raise ValueError('当前系统无法确认Windows存储位置')
    original = Path(path).expanduser().absolute()
    try:
        if os.name == 'nt':
            # Check original drive before resolving any link into a network share.
            _check_windows_path(raw if PureWindowsPath(raw).drive else original)
        p = _resolve_target(original)
        if os.name == 'nt':
            _check_windows_path(p)
    except (OSError, RuntimeError):
        raise ValueError('无法确认路径的本地真实目标') from None
    # Check both the alias and the real target for repository/sync placement.
    for candidate in (original, p):
        if candidate == REPO or REPO in candidate.parents or any((x / '.git').is_file() or (x / '.git' / 'HEAD').is_file() for x in (candidate, *candidate.parents)):
            raise ValueError('真实数据路径不得位于 Git 仓库内')
        if any(any(part.casefold() == word or part.casefold().startswith((word + ' - ', word + ' (', word + ' sync'))
                   for word in BLOCKED) for part in candidate.parts):
            raise ValueError('禁止已知云同步路径')
        for key in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
            if os.environ.get(key):
                sync = Path(os.environ[key]).resolve(strict=False)
                if candidate == sync or sync in candidate.parents:
                    raise ValueError('禁止云同步路径')
    return p


def root(path):
    p = local_path(path)
    if not (p / '.local-only').is_file():
        raise ValueError('请先 init 并确认此路径不在任何云同步范围')
    return p


def initialize(path, acknowledged=False):
    p = local_path(path)
    if not acknowledged:
        raise ValueError('需显式确认本地非同步存储；无法自动识别所有同步软件')
    p.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = p / '.local-only'
    if marker.is_symlink():
        raise ValueError('本地确认标记不能为链接')
    marker.touch(exist_ok=True)
    return p


def save(path, category, document):
    validate_document(document, {'snapshots': 'snapshot', 'market': 'market'}.get(category, 'invalid'))
    base = root(path)
    p = local_path(base / category)
    if base not in p.parents:
        raise ValueError('输出子目录不得链接至数据目录之外')
    p.mkdir(exist_ok=True, mode=0o700)
    dest = p / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex + '.json')
    with dest.open('x', encoding='utf-8') as f:
        json.dump(document, f, ensure_ascii=False, indent=2, allow_nan=False)
    return dest


def read_private(path, base, kind=None):
    p = local_path(path)
    if root(base) not in p.parents:
        raise ValueError('输入必须位于已确认的本地数据目录')
    def unique_object(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('JSON含重复字段')
            obj[key] = value
        return obj

    def reject_constant(value):
        raise ValueError('JSON含非法数值常量')

    try:
        with p.open('rb') as f:
            raw = f.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError('JSON超出大小上限')
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError('JSON格式无效') from None
    return validate_document(document, kind)
