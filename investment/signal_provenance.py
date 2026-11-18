"""One source/time boundary for signal views and semantic event statistics.

Saved books are claims, not evidence. Bind their dates and copied conditions to
the original report before classifying them. Never repair a historical source.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json

from .holdings import _safe_path, _canonical

BJ = timezone(timedelta(hours=8))
MAX_RECORD_SECONDS = 360
REVISION = 1
CONDITION_FIELDS = ('state', 'through', 'modelFingerprint', 'metConditions',
                    'unmetConditions', 'nextCondition', 'invalidation', 'tradeAdmission', 'history')
SIGNAL_FIELDS = {'state': 'state', 'researchThrough': 'through', 'modelFingerprint': 'modelFingerprint',
                 'metConditions': 'metConditions', 'unmetConditions': 'unmetConditions',
                 'entryCondition': 'nextCondition', 'invalidation': 'invalidation',
                 'tradeAdmission': 'tradeAdmission', 'retrospectiveEvidence': 'history'}


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('SIGNAL_TIME_WITHOUT_ZONE')
    return result


def load_reports(root, current_report=None):
    """Read bounded original archives; conflicting IDs are never trusted."""
    base = _safe_path(Path(root) / 'investment/work', missing=True)
    reports, conflicts = {}, set()
    for path in (base / 'runs').glob('*/report.json'):
        try:
            path = _safe_path(path, file=True)
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            raw = path.read_bytes()
            report = json.loads(raw)
            identifier = report['id']
            if identifier in reports and _canonical(reports[identifier][0]) != _canonical(report):
                conflicts.add(identifier)
            reports[identifier] = (report, hashlib.sha256(raw).hexdigest())
        except (OSError, ValueError, KeyError, TypeError):
            continue
    if current_report is not None:
        identifier = current_report['id']
        if identifier in reports and _canonical(reports[identifier][0]) != _canonical(current_report):
            conflicts.add(identifier)
        reports[identifier] = (current_report, None)
    for identifier in conflicts:
        reports.pop(identifier, None)
    return reports


def classify(book, signal, report, clock):
    """Return a factual cohort; malformed/detached claims remain unverified."""
    result = {'cohort': 'unverified', 'proof': False, 'codes': [], 'quote': {}, 'features': {}}
    try:
        if not all(isinstance(value, dict) for value in (book, signal, report)):
            raise ValueError('SIGNAL_SOURCE_SHAPE_INVALID')
        if (book.get('format') != 'goose-research-signal-book' or type(book.get('version')) is not int
                or book['version'] != 1):
            raise ValueError('SIGNAL_BOOK_FORMAT_INVALID')
        if (report.get('format') != 'goose-live-research' or type(report.get('version')) is not int
                or report['version'] != 1 or report.get('id') != book.get('reportId')
                or signal.get('sourceReportId') != report['id']):
            raise ValueError('SIGNAL_SOURCE_REPORT_MISSING_OR_MISMATCH')
        generated, recorded = instant(report['generatedAt']), instant(book['recordedAt'])
        if (instant(book['signalAt']) != generated or instant(signal['signalAt']) != generated
                or recorded < generated or generated > clock or recorded > clock
                or signal.get('signalDay') != generated.astimezone(BJ).date().isoformat()):
            raise ValueError('SIGNAL_BOOK_TIME_SOURCE_MISMATCH')
        items = report.get('researchStates', {}).get('items', [])
        source = {item['symbol']: item for item in items}
        symbols = [item['symbol'] for item in book['signals']]
        if len(source) != len(items) or len(set(symbols)) != len(symbols) or set(symbols) != set(source):
            raise ValueError('SIGNAL_POOL_INCOMPLETE_OR_DUPLICATE')
        item = source[signal['symbol']]
        from .radar_market import catalog
        if signal.get('benchmarkSymbol') != catalog()['benchmark']:
            raise ValueError('SIGNAL_BENCHMARK_SOURCE_MISMATCH')
        digest = hashlib.sha256(_canonical({key: item.get(key) for key in CONDITION_FIELDS})).hexdigest()
        if (signal.get('sourceEvidenceSha256') != digest
                or any(signal.get(left) != item.get(right) for left, right in SIGNAL_FIELDS.items())):
            raise ValueError('SIGNAL_CONDITION_SOURCE_MISMATCH')
        through = item.get('through')
        if through is not None and (datetime.fromisoformat(through).date() > generated.astimezone(BJ).date()
                                    or datetime.fromisoformat(through).date().isoformat() != through):
            raise ValueError('SIGNAL_RESEARCH_THROUGH_INVALID')
        radar = next((item for item in report.get('opportunityRadar', {}).get('items', [])
                      if item.get('symbol') == signal['symbol']), {})
        holding = next((item for item in report.get('instruments', [])
                        if item.get('symbol') == signal['symbol']), {})
        quote = holding.get('quote') or radar.get('quality', {}).get('quote') or {}
        if not isinstance(quote, dict) or not isinstance(radar.get('features') or {}, dict):
            raise ValueError('SIGNAL_SOURCE_SHAPE_INVALID')
        result.update(quote=quote, features=radar.get('features') or {})
        simulated = bool(book.get('synthetic') or report.get('synthetic') or report.get('testData'))
        prospective = (not simulated and not report.get('cachedReport') and book.get('prospective') is True
                       and book.get('sourceCached') is False and book.get('origin') == 'recorded-with-report'
                       and 0 <= (recorded - generated).total_seconds() <= MAX_RECORD_SECONDS)
        later = any(stamp is not None and instant(stamp) > generated
                    for stamp in (quote.get('sourceTimestamp'), quote.get('fetchedAt'), item.get('historyFetchedAt')))
        if later:
            if prospective:
                raise ValueError('SIGNAL_INPUT_NOT_VISIBLE_AT_REPORT')
            result['codes'].append('RECONSTRUCTED_INPUT_AFTER_DECLARED_GENERATION')
        if book.get('prospective') is True and not prospective and not simulated:
            raise ValueError('UNSUPPORTED_PROSPECTIVE_LABEL')
        result.update(proof=True, cohort='simulation' if simulated else 'prospective' if prospective else 'reconstructed')
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as error:
        code = str(error)
        result['codes'].append(code if code.isascii() and code.replace('_', '').isalnum() and len(code) <= 100 else 'SIGNAL_SOURCE_INVALID')
    return result
