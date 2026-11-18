"""Credential-free Linux acceptance: real calculations on fictional inputs.

Offline fictional test only. No real account, provider request, Cloudflare,
Bark, cache upload or artifact upload is used.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / 'v32', ROOT / 'v33'):
    sys.path.insert(0, str(source))
import socket
def forbidden_network(*args, **kwargs):
    raise AssertionError("OFFLINE_LINUX_TEST_NETWORK_FORBIDDEN")
socket.socket.connect = forbidden_network
import urllib.request
urllib.request.OpenerDirector.open = forbidden_network

BJ = timezone(timedelta(hours=8))



def fixture_inputs(now=None):
    """Fixed holdout-sized fictional history; no user prices or holdings."""
    from investment import radar_market
    from goose_research.market import xshg_sessions, calendar_supported_through, validate_dataset
    now = now or datetime.now(timezone.utc)
    through = radar_market.complete_session(now)
    days = xshg_sessions('2024-12-02', through)
    assert len(days) >= 400, 'FIXED_HOLDOUT_FIXTURE_TOO_SHORT'
    catalog = radar_market.catalog()
    histories, quotes, quality = {}, {}, {}
    for index, spec in enumerate(catalog['instruments']):
        tick = Decimal(spec['tick'])
        base = Decimal('10') if spec['kind'] == 'stock' else Decimal('1')
        rows = []
        for offset, day in enumerate(days):
            close = base + tick * (offset + index)
            rows.append({'date':day, 'open':str(close), 'close':str(close),
                         'high':str(close+tick), 'low':str(close-tick), 'volume':100000000,
                         'publishedAt':day+'T15:30:00+08:00'})
        raw = validate_dataset({'format':'goose-market-dataset', 'version':1, 'symbol':spec['symbol'],
            'kind':spec['kind'], 'source':'fictional-linux-acceptance-only',
            'sourceUrl':'https://example.invalid/fictional-linux-acceptance',
            'adjustment':'none', 'currency':'CNY', 'priceUnit':'CNY-per-share', 'fetchedAt':now.isoformat(),
            'calendar':{'name':'XSHG', 'sessions':days, 'supportedThrough':calendar_supported_through()},
            'bars':rows, 'quality':{'status':'validated', 'unitVerified':True, 'crossValidated':False,
                'corporateActionsVerified':False, 'unitEvidence':'FICTIONAL_TEST_UNITS_ONLY_NOT_PROVIDER_EVIDENCE',
                'warnings':['FICTIONAL_ENGINEERING_ACCEPTANCE_NOT_REAL_MARKET_DATA']}})
        histories[spec['symbol']] = {'raw':raw, 'adjusted':{'symbol':spec['symbol'], 'adjustment':'qfq',
            'bars':deepcopy(rows), 'quality':{'status':'validated-basis-only', 'ohlcAndCalendarValidated':True}}}
        quote = {'symbol':spec['symbol'], 'name':spec['name'], 'source':'tencent-quote',
            'sourceTimestamp':through+'T15:00:00+08:00', 'fetchedAt':now.isoformat(),
            'currency':'CNY', 'priceUnit':'CNY-per-share', 'adjustment':'none',
            'price':rows[-1]['close'], 'previousClose':rows[-2]['close'], 'volume':100000000,
            'warnings':['FICTIONAL_ENGINEERING_ACCEPTANCE_ONLY'],
            'quality':{'status':'historical-snapshot', 'unitVerified':True, 'intradaySignalAllowed':False}}
        quotes[spec['symbol']] = quote
        quality[spec['symbol']] = {'status':'validated-public-history', 'quote':quote, 'historyFetchedAt':now.isoformat()}
    radar = {'catalog':catalog, 'through':through, 'synthetic':False,
             'histories':histories, 'quality':quality}
    # These quality flags exercise the existing production calculations only
    # inside a disposable fixture root; every source/warning identifies fiction.
    initial = {'format':'goose-investment-market', 'synthetic':False, 'quotes':quotes,
               'histories':{symbol:history['raw'] for symbol,history in histories.items()},
               'adjustedHistories':{symbol:history['adjusted'] for symbol,history in histories.items()},
               'diagnostics':[], 'context':{'fixtureOnly':True}}
    return initial, radar


def synthetic_acceptance():
    from investment import engine, holdings, strategy_lab
    started = time.monotonic()
    initial, radar = fixture_inputs()
    with tempfile.TemporaryDirectory(prefix='goose-linux-fictional-') as directory:
        root = Path(directory)
        for symbol, broker, quantity, cash in [('600900.SH','华宝证券',100,'1000'),
                                               ('515450.SH','银河证券',200,'2000')]:
            preview = holdings.parse_manual([{'symbol':symbol, 'quantity':quantity, 'costPrice':'1.2345'}],
                radar['through'], broker, available_cash=cash)
            holdings.commit_preview(preview, root/'investment/work/private/holdings', confirmed=True)
        # Only acquisition boundaries are replaced. The original accounting,
        # risk, indicators, five strategies, radar, source guard, event studies
        # and immutable report writer all execute. Any stray network/Bark fails.
        with patch('investment.market.collect_market', return_value=initial), \
             patch('investment.market.collect_quotes', return_value={'quotes':initial['quotes'],'diagnostics':[]}), \
             patch('investment.radar.collect', return_value=radar), \
             patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('NETWORK_FORBIDDEN_IN_FICTIONAL_RUN')), \
             patch('socket.socket.connect', side_effect=AssertionError('NETWORK_FORBIDDEN_IN_FICTIONAL_RUN')), \
             patch('investment.delivery.send_daily', side_effect=AssertionError('BARK_FORBIDDEN_IN_ACCEPTANCE')):
            report = engine.run_analysis(root)
        files = list((root/'investment/work/runs').glob('*/report.json'))
        assert len(files) == 1, 'IMMUTABLE_REPORT_MISSING'
        saved = json.loads(files[0].read_text(encoding='utf8'))
        assert saved['id'] == report['id'], 'REPORT_ID_MISMATCH'
        assert len(report['portfolio']['positions']) == 2, 'FICTIONAL_POSITIONS_MISSING'
        assert len(report['researchStates']['items']) == 16, 'RESEARCH_POOL_MISSING'
        assert report['realOrdersEnabled'] is False and report['notificationSent'] is False, 'SIDE_EFFECT_GUARD'
        labs = [item['lab'] for item in report['opportunityRadar']['items'] if item.get('lab')]
        assert len(labs) == 16, 'FULL_FIVE_STRATEGY_CALCULATIONS_MISSING'
        assert all(lab['modelRevision'] == 4 and set(lab['strategies']) == set(strategy_lab.STRATEGIES) for lab in labs)
        assert report['signalTracking']['provenanceRejectedCount'] == 0, 'INFLIGHT_SOURCE_BINDING_FAILED'
        assert report['signalReview']['cohorts']['unverified'] == 0, 'SEMANTIC_SOURCE_BINDING_FAILED'
        return {'status':'passed', 'fixtureOnly':True, 'realAccountRead':False, 'networkRequests':0,
                'realBarkRequests':0, 'realOrders':0, 'instrumentCount':len(report['instruments']),
                'researchPoolCount':16, 'strategyStudyCount':len(labs)*len(strategy_lab.STRATEGIES),
                'sourceGuard':'both-production-readers', 'immutableReportVerified':True,
                'seconds':round(time.monotonic()-started,3),
                'meaning':'Fictional engineering run; no investment result or live-market certification.'}


if __name__ == "__main__":
    assert platform.system() == "Linux", "ACTUAL_LINUX_REQUIRED"
    print(json.dumps({"platform":platform.system(),"python":platform.python_version(),"result":synthetic_acceptance()},sort_keys=True))
