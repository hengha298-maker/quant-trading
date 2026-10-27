"""Manual one-shot checks. Exclusive outputs only below this checkout's v33/work."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from uuid import uuid4

from .health import build_health
from .network import SOURCES, fetch_observation
from .reports import phone_report, markdown, canonical
from .json_limits import check_nesting

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work'


def output_directory(request=None):
    destination = Path(request).absolute() if request else WORK / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:8])
    for path in [destination, *destination.parents]:
        if path.exists() and (path.is_symlink() or path.is_junction()):
            raise ValueError('OUTPUT_REPARSE_REJECTED')
    resolved = destination.resolve()
    if not resolved.is_relative_to(WORK.resolve()) or resolved == WORK.resolve():
        raise ValueError('OUTPUT_ONLY_NEW_DIRECTORY_UNDER_V33_WORK')
    WORK.mkdir(exist_ok=True)
    resolved.mkdir(parents=True, exist_ok=False)
    return resolved


def load_json(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('INPUT_FILE_LIMIT')
    def pairs(items):
        doc = {}
        for k, v in items:
            if k in doc or k in {'__proto__', 'prototype', 'constructor'}:
                raise ValueError('INPUT_DUPLICATE_OR_UNSAFE_KEY')
            doc[k] = v
        return doc
    try:
        text = path.read_text(encoding='utf-8-sig')
        check_nesting(text)
        return json.loads(text, object_pairs_hook=pairs,
            parse_float=lambda _: (_ for _ in ()).throw(ValueError('INPUT_FLOAT_NOT_SUPPORTED_USE_DECIMAL_TEXT')),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('INPUT_NONFINITE_NUMBER')))
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        raise ValueError('INPUT_JSON_INVALID') from None


def write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def run_check(*, sources=None, replay=None, history=None, out=None):
    start = time.perf_counter()
    selected = sources if sources is not None else list(SOURCES)
    if not selected or len(set(selected)) != len(selected) or any(s not in SOURCES for s in selected):
        raise ValueError('EXPLICIT_DISTINCT_SOURCES_REQUIRED')
    # Validate all local inputs and output isolation before making any request.
    dataset = load_json(history) if history else None
    doc = load_json(replay) if replay else None
    if doc is not None and (type(doc) is not dict or doc.get('format') != 'goose-intraday-replay' or type(doc.get('version')) is not int or doc['version'] != 1 or type(doc.get('observations')) is not list or not 1 <= len(doc['observations']) <= 2 or type(doc.get('fictional')) is not bool):
        raise ValueError('REPLAY_SCHEMA_INVALID')
    folder = output_directory(out)
    raw_files = {}
    if doc is not None:
        observations = doc['observations']
        mode = 'synthetic' if doc['fictional'] else 'replay'
    else:
        mode = 'live'
        observations = []
        # Independent explicit observations, not a fallback that hides failure.
        # 2027 is rejected before networking; no weekday inference.
        from .health import context
        if context(datetime.now(timezone.utc))['calendar'] == 'unverified-or-unavailable':
            observations = [{'source': s, 'errors': ['CALENDAR_UNVERIFIED_NO_NETWORK']} for s in selected]
        else:
            for source in selected:
                observation, raw = fetch_observation(source)
                observations.append(observation)
                if raw is not None:
                    name = source + '.raw'
                    with (folder / name).open('xb') as stream:
                        stream.write(raw)
                    raw_files[name] = sha256(raw).hexdigest()
    health = build_health(observations, mode=mode, dataset=dataset)
    compatible = phone_report(health)
    write_json(folder / 'observations.json', {'format': 'goose-intraday-replay', 'version': 1,
        'fictional': mode == 'synthetic', 'observations': observations})
    write_json(folder / 'health.json', health)
    write_json(folder / 'goose-report-v31.json', compatible)
    with (folder / 'report.md').open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(markdown(health))
    elapsed = round((time.perf_counter() - start) * 1000)
    ended = datetime.now(timezone.utc)
    from .health import context
    complete_in_window = mode == 'live' and health['manualWindowChecksPassed'] and context(ended)['manualWindow']
    manifest = {'format': 'goose-intraday-run', 'version': 1, 'mode': mode,
        'startedBy': 'manual-cli', 'endedAt': ended.isoformat(), 'elapsedMs': elapsed,
        'manualWindowCheckComplete': bool(complete_in_window),
        'decisionAllowed': False, 'notificationSent': False, 'newTaskCreated': False,
        'files': {p.name: sha256(p.read_bytes()).hexdigest() for p in folder.iterdir() if p.is_file()},
        'rawResponses': raw_files, 'healthCanonicalSha256': sha256(canonical(health)).hexdigest()}
    write_json(folder / 'run.json', manifest)
    return {'output': str(folder), 'status': health['status'], 'manualWindowChecksPassed': bool(complete_in_window),
        'intradayReliabilityVerified': False, 'decisionAllowed': False, 'notificationSent': False,
        'elapsedMs': elapsed, 'codes': health['codes']}


def main(argv=None):
    parser = argparse.ArgumentParser(description='V3.3 manual public quote health; never sends Bark or trades')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('check', help='one explicit observation per selected source, real clock only')
    check.add_argument('--sources', nargs='+', choices=list(SOURCES), default=list(SOURCES))
    check.add_argument('--history', help='optional independently validated daily context ending previous session')
    check.add_argument('--out')
    replay = sub.add_parser('replay', help='offline input; cannot verify current intraday availability')
    replay.add_argument('--input', required=True); replay.add_argument('--out')
    args = parser.parse_args(argv)
    try:
        result = run_check(sources=getattr(args, 'sources', None), history=getattr(args, 'history', None),
            replay=getattr(args, 'input', None), out=args.out)
        print(json.dumps(result, ensure_ascii=True))
        return 0 if result['manualWindowChecksPassed'] else 2
    except (ValueError, OSError) as error:
        import re
        code = str(error) if isinstance(error, ValueError) and re.fullmatch(r'[A-Z0-9_]{1,100}', str(error)) else 'LOCAL_INPUT_OR_OUTPUT_FAILED'
        print(json.dumps({'status': 'failed', 'code': code, 'notificationSent': False, 'decisionAllowed': False}))
        return 2
