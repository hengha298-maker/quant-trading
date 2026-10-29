"""Recorded-schema fictional responses; no external network in these checks."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import hashlib,json,tempfile,unittest
from unittest.mock import patch
from investment import radar_market as rm
from investment.holdings import _canonical
from goose_research.market import xshg_sessions

NOW=datetime.fromisoformat('2026-10-29T21:00:00+08:00')
SPEC=next(s for s in rm.catalog()['instruments'] if s['symbol']=='510300.SH')


def quote(**changes):
    fields=['']*51
    for key,value in {0:'1',1:SPEC['aliases'][0],2:'510300',3:'1.200',4:'1.200',5:'1.200',6:'1000000',30:'20261029150000',33:'1.200',34:'1.200',36:'1000000',37:'12000'}.items():fields[key]=value
    for key,value in changes.items():fields[int(key)]=value
    return ('v_sh510300="'+'~'.join(fields)+'";').encode('gbk')


class RadarMarketTests(unittest.TestCase):
    def test_identity_units_missing_and_large_change(self):
        parsed=rm.parse_quotes(quote(),{SPEC['symbol']:SPEC})['quotes'][SPEC['symbol']]
        self.assertEqual(100000000,parsed['volume']);self.assertEqual('120000000',parsed['amountCNY'])
        for kwargs,code in [({'1':'unknown'},'NAME'),({'36':'1'},'VOLUME'),({'37':'12'},'UNIT'),({'4':'0.200'},'SCALE')]:
            result=rm.parse_quotes(quote(**kwargs),{SPEC['symbol']:SPEC})
            self.assertEqual({},result['quotes']);self.assertIn(code,result['errors'][SPEC['symbol']])
        with self.assertRaisesRegex(ValueError,'IDENTITY'):rm.parse_quotes(quote()+b'\n'+quote(),{SPEC['symbol']:SPEC})

    def test_complete_day_and_unknown_cross_year(self):
        self.assertEqual('2026-10-28',rm.complete_session(datetime.fromisoformat('2026-10-29T14:38:00+08:00')))
        self.assertEqual('2026-10-29',rm.complete_session(NOW))
        with self.assertRaises(ValueError):rm.complete_session(datetime.fromisoformat('2027-01-04T14:38:00+08:00'))

    def test_offline_collection_cache_is_marked_synthetic_and_cannot_enter_live(self):
        cat={**rm.catalog(),'instruments':[SPEC]};calls=[]
        def attempt(source,symbol,url,parser,deadline=None):
            calls.append(source)
            if source.endswith('quotes'):raw=quote()
            else:
                param=parse_qs(urlparse(url).query)['param'][0].split(',');begin,end=param[2:4];basis=param[-1]
                rows=[[day,'1.200','1.200','1.200','1.200','1000000'] for day in xshg_sessions(begin,end)]
                raw=json.dumps({'code':0,'data':{'sh510300':{'qfqday' if basis=='qfq' else 'day':rows}}}).encode()
            obs={'value':parser(raw),'requestedAt':NOW.isoformat(),'receivedAt':NOW.isoformat(),'elapsedMs':1,'rawSha256':hashlib.sha256(raw).hexdigest(),'headers':{}}
            return obs,raw
        scratch=Path(__file__).resolve().parents[1]/'work/radar-market-tests';scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as root,patch('investment.radar_market.catalog',return_value=cat):
            result=rm.collect(root,now=NOW,attempt=attempt)
            self.assertTrue(result['synthetic']);self.assertEqual(3,len(calls))
            h=result['histories'][SPEC['symbol']];self.assertEqual('synthetic',h['raw']['quality']['status'])
            folder=Path(root)/'investment/work/radar/market';self.assertIsNotNone(rm._cached(folder,SPEC['symbol']))
            # A later live call must reject a fictional cache, not relabel it.
            with patch('investment.radar_market._attempt',side_effect=attempt):live=rm.collect(root,now=NOW)
            self.assertIsNone(live['histories'][SPEC['symbol']]);self.assertIn('RADAR_CACHED_TEST_DATA_NOT_PRODUCTION',live['quality'][SPEC['symbol']]['codes'])
            path=next(folder.glob('*.json'));wrapper=json.loads(path.read_text());wrapper['raw']['bars'][0]['close']='9';path.write_text(json.dumps(wrapper))
            with self.assertRaisesRegex(ValueError,'CORRUPT'):rm._cached(folder,SPEC['symbol'])

    def test_raw_only_response_cannot_impersonate_adjusted_history(self):
        raw=json.dumps({'code':0,'data':{'sh510300':{'day':[]}}}).encode()
        with self.assertRaisesRegex(ValueError,'BASIS'):rm._daily_parser(raw,SPEC['symbol'],'2026-10-29','2026-10-29','qfq')
