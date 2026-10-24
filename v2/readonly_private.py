"""Read ONLY explicit P1 selections via the pinned original read_private.

No discovery, network, writes, price fallback, or retained raw documents/paths.
"""
import json
from pathlib import Path

from readonly_projection import project_view

INPUT_ERROR = ('未能读取选定P1文件：请选择同一GooseResearchP1目录的snapshots快照及market行情JSON；'
               '目录须已有.local-only确认标记，位于本地非同步磁盘且不在Git仓库内。'
               '请核对JSON格式和数据资格。不会自动修复、挑选旧行情或修改原文件。')


def build_private_view(p1, snapshot_file, market_files, *, now=None):
    try:
        if not isinstance(market_files, (list, tuple)) or len(market_files) > 16:
            raise ValueError()
        snapshot = p1.storage.local_path(snapshot_file)
        if snapshot.parent.name != 'snapshots' or snapshot.suffix.lower() != '.json':
            raise ValueError()
        base = p1.storage.root(snapshot.parent.parent)
        files = [snapshot] + [p1.storage.local_path(f) for f in market_files]
        if len(set(files)) != len(files):
            raise ValueError()
        for path in files:
            if path.suffix.lower() != '.json' or not path.is_file():
                raise ValueError()
            # Also refuse a ZIP checkout even if it lacks .git metadata.
            if Path(__file__).resolve().parent.parent in path.parents:
                raise ValueError()
        if any(f.parent != base / 'market' for f in files[1:]):
            raise ValueError()
        if sum(f.stat().st_size for f in files) > 32_000_000:
            raise ValueError()
        data = p1.storage.read_private(snapshot, base, 'snapshot')
        markets = [p1.storage.read_private(f, base, 'market') for f in files[1:]]
        view = project_view(p1, data, markets, test_data=False, now=now)
        if len(view['accounts']) > 100 or len(view['quotes']) > 100 or any(
                len(a['positions']) > 1000 for a in view['accounts']):
            raise ValueError()
        if len(json.dumps(view, ensure_ascii=True, allow_nan=False).encode('ascii')) > 128000:
            raise ValueError()
        return view
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        raise ValueError(INPUT_ERROR) from None
