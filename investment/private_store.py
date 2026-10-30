"""Append-only private records and atomic publication of validated recoveries.

Only accounting data is eligible. Secrets, task controls and delivery claims are
deliberately outside the backup/restore namespace.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib, json, os, re
from .holdings import _safe_path, _canonical, HoldingsError

CATEGORIES = {'holdings', 'broker-events', 'broker-event-voids', 'ledger-statements'}

def private(root):
    return _safe_path(Path(root)/'investment/work/private', missing=True)

@contextmanager
def store_lock(folder):
    from .runtime_lock import analysis_mutex
    folder = _safe_path(folder, missing=True)
    folder.mkdir(parents=True, exist_ok=True)
    with analysis_mutex(folder):
        yield

def record_paths(folder):
    """Flat originals plus complete recovery generations; never pending stages."""
    folder = _safe_path(folder, missing=True)
    result = {}
    if folder.exists():
        for p in folder.iterdir():
            if p.name in {'.import.lock', '.analysis-owner.lock'}:
                continue
            _safe_path(p, file=True)
            if p.suffix != '.json':
                raise HoldingsError('STORE_UNKNOWN_FILE', '私有账本含未知文件，停止读取。')
            result[p.name] = p
    if folder.name not in CATEGORIES:
        return sorted(result.values(), key=lambda p:p.name)
    recovered = _safe_path(folder.parent/'recovered-ledgers', missing=True)
    if recovered.exists():
        for generation in sorted(recovered.iterdir()):
            _safe_path(generation)
            if not generation.is_dir() or not re.fullmatch(r'[0-9a-f]{32}', generation.name):
                raise HoldingsError('RECOVERY_INVALID', '恢复代目录异常。')
            manifest = _safe_path(generation/'manifest.json', file=True)
            try:
                doc = json.loads(manifest.read_text(encoding='utf8'))
                valid = doc['format']=='goose-ledger-recovery' and doc['version']==1 and type(doc['files']) is list and len(doc['files'])<=5000
                if valid:
                    keys=[]
                    for item in doc['files']:
                        if type(item) is not dict or set(item)!={'category','name','sha256'} or item['category'] not in CATEGORIES or not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}\.json',str(item['name'])) or not re.fullmatch(r'[a-f0-9]{64}',str(item['sha256'])):
                            valid=False;break
                        keys.append((item['category'],item['name']))
                    valid=valid and len(keys)==len(set(keys))
            except (KeyError,TypeError,ValueError): valid=False
            if not valid: raise HoldingsError('RECOVERY_INVALID','恢复清单损坏，未跳过。')
            allowed={'manifest.json',*[item['category'] for item in doc['files']]}
            for p in generation.iterdir():
                _safe_path(p)
                if p.name not in allowed:raise HoldingsError('RECOVERY_INVALID','恢复目录含未列入清单的文件。')
                if p.name in CATEGORIES:
                    expected={i['name'] for i in doc['files'] if i['category']==p.name}
                    for child in p.iterdir():
                        _safe_path(child,file=True)
                        if child.name not in expected:raise HoldingsError('RECOVERY_INVALID','恢复记录未列入校验清单。')
            for item in doc['files']:
                if item.get('category') != folder.name: continue
                name=item.get('name','')
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}\.json',name):
                    raise HoldingsError('RECOVERY_INVALID','恢复记录名异常。')
                p=_safe_path(generation/folder.name/name,file=True)
                if hashlib.sha256(p.read_bytes()).hexdigest()!=item.get('sha256'):
                    raise HoldingsError('RECOVERY_INVALID','恢复记录校验失败。')
                if name in result:
                    if result[name].read_bytes()!=p.read_bytes():
                        raise HoldingsError('RECOVERY_CONFLICT','恢复记录与现有记录冲突，停止读取。')
                else:result[name]=p
    return sorted(result.values(),key=lambda p:p.name)
