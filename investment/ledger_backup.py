"""Validated ledger transfer; encryption/password handling stays in WebCrypto.

Restore merges immutable records, never overwrites a file. A fully validated
generation becomes visible via one same-volume directory rename. Interrupted
staging is invisible to readers. Credentials and scheduling are never restored.
"""
from pathlib import Path
import base64,hashlib,json,os,re,tempfile,uuid
from .holdings import _safe_path,_canonical,_read_snapshot,_now,HoldingsError
from .private_store import private,record_paths,store_lock,CATEGORIES
from .ledger import fail,load_batches

MAX_BYTES=12*1024*1024
MAX_FILES=5000

def fingerprint(files):
    return hashlib.sha256(_canonical(sorted((f['category'],f['name'],f['sha256']) for f in files))).hexdigest()

def _export(root):
    files=[];total=0
    for category in sorted(CATEGORIES):
        if (private(root)/category/'.import.lock').exists():fail('BACKUP_STORE_BUSY','账本导入锁存在；停止备份/恢复，不能猜测或清除锁。')
        for p in record_paths(private(root)/category):
            raw=p.read_bytes();total+=len(raw)
            if total>MAX_BYTES or len(files)>=MAX_FILES:fail('BACKUP_SIZE','账本超过本版12MiB/5000文件上限，未截断导出；请保留原件。')
            files.append({'category':category,'name':p.name,'sha256':hashlib.sha256(raw).hexdigest(),'content':base64.b64encode(raw).decode('ascii')})
    return {'format':'goose-investment-ledger-state','version':1,'createdAt':_now().isoformat(),
            'scope':sorted(CATEGORIES),'files':files,'manifestSha256':fingerprint(files),
            'excluded':['Bark/access keys','task controls/delivery claims','broker credentials','market/report caches','raw broker source files'],
            'costPolicy':'explicit-per-statement; no automatic cost rewrite'}

def export_state(root):
    with store_lock(private(root)):
        value=_export(root)
        validate_state(value,scratch_parent=private(root))
        return value

def _decode(value):
    if type(value) is not dict or value.get('format')!='goose-investment-ledger-state' or type(value.get('version')) is not int or value['version']!=1:fail('BACKUP_VERSION','账本备份格式/版本无效。')
    files=value.get('files')
    try:
        stamp=__import__('datetime').datetime.fromisoformat(value['createdAt'])
        if stamp.tzinfo is None:raise ValueError()
    except (KeyError,TypeError,ValueError):fail('BACKUP_METADATA','备份时间戳无效。')
    if type(files) is not list or len(files)>MAX_FILES or value.get('scope')!=sorted(CATEGORIES):fail('BACKUP_SCOPE','账本备份范围无效。')
    decoded={};total=0
    for f in files:
        if type(f) is not dict or set(f)!={'category','name','sha256','content'} or f['category'] not in CATEGORIES or not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}\.json',str(f['name'])):fail('BACKUP_PATH','备份含未允许的路径或字段。')
        key=(f['category'],f['name'])
        if key in decoded:fail('BACKUP_DUPLICATE','备份文件条目重复。')
        try:raw=base64.b64decode(f['content'],validate=True)
        except (ValueError,TypeError):fail('BACKUP_CONTENT','备份记录编码无效。')
        total+=len(raw)
        if total>MAX_BYTES or len(raw)>5*1024*1024:fail('BACKUP_SIZE','备份超过保护上限。')
        if hashlib.sha256(raw).hexdigest()!=f['sha256']:fail('BACKUP_CHECKSUM','备份记录校验失败。')
        try:doc=json.loads(raw)
        except (ValueError,UnicodeError):fail('BACKUP_CONTENT','备份记录JSON无效。')
        if type(doc) is not dict:fail('BACKUP_CONTENT','备份记录不是有效对象。')
        decoded[key]=raw
    if value.get('manifestSha256')!=fingerprint(files):fail('BACKUP_CHECKSUM','备份总清单校验失败。')
    return decoded

def _write_virtual(root,decoded):
    for (category,name),raw in decoded.items():
        folder=private(root)/category;folder.mkdir(parents=True,exist_ok=True)
        with (folder/name).open('xb') as stream:stream.write(raw)

def _validate_records(root):
    from .statements import load
    for p in record_paths(private(root)/'holdings'):
        s=_read_snapshot(p)
        if s.get('snapshotId')!=p.stem:fail('BACKUP_HOLDINGS_ID','持仓快照编号与文件不一致。')
    load_batches(root,include_void=True);load(root)

def validate_state(value,*,scratch_parent=None):
    decoded=_decode(value)
    # Caller production data is never used as a restore target during validation.
    with tempfile.TemporaryDirectory(prefix='.ledger-validation-',dir=scratch_parent) as scratch:
        virtual=Path(scratch);_write_virtual(virtual,decoded);_validate_records(virtual)
    return decoded

def _plan(root,value):
    incoming=validate_state(value,scratch_parent=private(root));current=_export(root);existing=_decode(current)
    conflicts=[];new={};identical=0
    for key,raw in incoming.items():
        if key in existing:
            if raw!=existing[key]:conflicts.append({'category':key[0],'name':key[1],'reason':'同编号内容不同'})
            else:identical+=1
        else:new[key]=raw
    # A backup cannot silently undo a currently active import as a side effect.
    for (category,name),raw in new.items():
        if category=='broker-event-voids' and ('broker-events',name) in existing:
            conflicts.append({'category':category,'name':name,'reason':'将撤销现有有效批次，不能自动合并'})
    if not conflicts:
        with tempfile.TemporaryDirectory(prefix='goose-ledger-merge-check-',dir=private(root)) as scratch:
            virtual=Path(scratch);_write_virtual(virtual,{**existing,**new})
            try:
                _validate_records(virtual)
                from .holdings import load_latest_by_broker
                current_accounts={s['source']['broker']:s for s in load_latest_by_broker(private(root)/'holdings')}
                for account in load_latest_by_broker(private(virtual)/'holdings'):
                    prior=current_accounts.get(account['source']['broker'])
                    if prior and prior['snapshotId']!=account['snapshotId']:
                        conflicts.append({'category':'holdings','reason':'将改变现有有效持仓基准；请先单独核对，不自动替换'})
            except (ValueError,OSError):conflicts.append({'category':'semantic','reason':'合并后有重复成交、孤立撤销或账本结构冲突'})
    return {'format':'goose-ledger-restore-preview','version':1,'source':value,'targetDigest':current['manifestSha256'],
            'newFileCount':len(new),'identicalCount':identical,'conflicts':conflicts,'sourceCreatedAt':value['createdAt'],
            'scope':value['scope'],'policy':'merge-only-never-overwrite','generatedAt':_now().isoformat()},new

def preview_restore(root,value):
    with store_lock(private(root)):return _plan(root,value)[0]

def restore(root,preview,confirmed=False,*,before_publish=None):
    if confirmed is not True or preview.get('format')!='goose-ledger-restore-preview':fail('RESTORE_CONFIRM','先验证预览，再明确确认合并。')
    with store_lock(private(root)):
        plan,new=_plan(root,preview['source'])
        if plan['targetDigest']!=preview['targetDigest']:fail('RESTORE_TARGET_CHANGED','预览后账本已变化，请重新预览；未覆盖数据。')
        if plan['conflicts']:fail('RESTORE_CONFLICT','存在恢复冲突，未改动有效账本。')
        if not new:return {'status':'already-present','restoredFiles':0}
        recovered=_safe_path(private(root)/'recovered-ledgers',missing=True);recovered.mkdir(parents=True,exist_ok=True)
        generation=uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix='.pending-ledger-',dir=private(root)) as scratch:
            virtual=Path(scratch);_write_virtual(virtual,new);target=private(virtual)
            manifest={'format':'goose-ledger-recovery','version':1,'createdAt':_now().isoformat(),
                      'files':[{'category':c,'name':n,'sha256':hashlib.sha256(raw).hexdigest()} for (c,n),raw in new.items()]}
            with (target/'manifest.json').open('xb') as f:f.write(_canonical(manifest));f.flush();os.fsync(f.fileno())
            # Full union was validated above. Every staged byte is checked before
            # publication; a simulated/actual write failure publishes nothing.
            for (c,n),raw in new.items():
                if (target/c/n).read_bytes()!=raw:fail('RESTORE_WRITE_FAILED','恢复暂存验证失败。')
            if before_publish:before_publish()
            os.rename(target,recovered/generation)
        return {'status':'restored-merge-only','restoredFiles':len(new),'generation':generation}
