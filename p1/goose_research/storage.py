import json
import os
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[2]
BLOCKED = ('onedrive', 'dropbox', 'icloud', 'google drive', 'googledrive', 'box', 'synologydrive')


def local_path(path):
    p = Path(path).expanduser().resolve()
    if p == REPO or REPO in p.parents or any((x / '.git').is_file() or (x / '.git' / 'HEAD').is_file() for x in (p, *p.parents)):
        raise ValueError('真实数据路径不得位于 Git 仓库内')
    if any(any(word in part.lower() for word in BLOCKED) for part in p.parts):
        raise ValueError('禁止已知云同步路径')
    for key in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial'):
        if os.environ.get(key):
            sync = Path(os.environ[key]).resolve()
            if p == sync or sync in p.parents:
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
    (p / '.local-only').touch(exist_ok=True)
    return p


def save(path, category, document):
    base = root(path)
    p = local_path(base / category)
    if base not in p.parents:
        raise ValueError('输出子目录不得链接至数据目录之外')
    p.mkdir(exist_ok=True, mode=0o700)
    dest = p / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex + '.json')
    with dest.open('x', encoding='utf-8') as f:
        json.dump(document, f, ensure_ascii=False, indent=2, allow_nan=False)
    return dest


def read_private(path, base):
    p = local_path(path)
    if root(base) not in p.parents:
        raise ValueError('输入必须位于已确认的本地数据目录')
    return json.loads(p.read_text(encoding='utf-8'))
