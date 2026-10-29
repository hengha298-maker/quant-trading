"""Fictional factor and integration checks, no network and no real accounts."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal as D
import unittest
from unittest.mock import patch
from investment import radar,engine
from investment.tests.test_strategy_lab import history


def market():
    instruments=[];histories={};quality={}
    for kind,prefix,count in [('etf','510',6),('stock','600',5)]:
        for i in range(count):
            symbol=prefix+str(i).zfill(3)+'.SH'
            instruments.append({'symbol':symbol,'name':'FICTIONAL '+symbol,'kind':kind,'category':'fictional','tick':'.001' if kind=='etf' else '.01','limitBps':1000})
            h=history(symbol,kind);h['raw']['quality']['status']='validated';histories[symbol]=h;quality[symbol]={'status':'fictional-unit-fixture'}
    return {'catalog':{'version':1,'reviewedOn':'2026-10-29','benchmark':instruments[0]['symbol'],'instruments':instruments,'selection':'FICTIONAL TEST ONLY'},'through':'2026-10-29','synthetic':False,'histories':histories,'quality':quality}


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.m=market();self.portfolio={'asOf':'2026-10-28','status':'stale-snapshot','availableCash':None,'concentration':'60','positions':[{'symbol':'510000.SH'}]}
        self.lab=patch('investment.radar.cached_suite',return_value={'strategies':{'buy_hold':{}},'fixture':True});self.lab.start();self.addCleanup(self.lab.stop)
        self.trust=patch('investment.trust_research.research',return_value=({'status':'unavailable','fixture':True},None));self.trust.start();self.addCleanup(self.trust.stop)

    def test_explainable_score_not_probability_and_cash_not_budget(self):
        r=radar.build('unused',self.m,self.portfolio)
        self.assertIsNone(r['accountConstraints']['buyQuantity']);self.assertFalse(r['accountConstraints']['cashVerifiedCurrent'])
        for i in r['items']:
            if i['score']:
                self.assertEqual(100,sum(p['maxPoints'] for p in i['score']['parts']))
                self.assertEqual(i['score']['value'],sum(p['points'] for p in i['score']['parts']))
                self.assertIn('不是未来收益概率',i['score']['meaning'])
        self.assertTrue(any('相关性高' in reason for i in r['items'] for reason in i['rejections']))
        self.assertFalse(next(i for i in r['items'] if i['symbol']=='510000.SH')['eligible'])

    def test_missing_held_history_blocks_candidates(self):
        self.portfolio['positions']=[{'symbol':'699999.SH'}]
        r=radar.build('unused',self.m,self.portfolio)
        self.assertTrue(all(not g['candidates'] for g in r['groups'].values()))
        self.assertTrue(any('相关性证据不完整' in reason for i in r['items'] for reason in i['rejections']))

    def test_sparse_group_refuses_ranking_and_synthetic_rejected(self):
        for s in list(self.m['histories'])[2:6]:self.m['histories'][s]=None
        r=radar.build('unused',self.m,self.portfolio)
        self.assertEqual('insufficient-valid-members',r['groups']['etf']['status']);self.assertEqual([],r['groups']['etf']['candidates'])
        self.m['synthetic']=True
        with self.assertRaisesRegex(ValueError,'SYNTHETIC'):radar.build('unused',self.m,self.portfolio)

    def test_no_padding_and_held_symbols_never_new_candidates(self):
        self.portfolio['concentration']=None
        r=radar.build('unused',self.m,self.portfolio)
        for g in r['groups'].values():
            self.assertLessEqual(len(g['candidates']),5)
            self.assertTrue(all(not i['isHeldInSnapshot'] for i in g['candidates']))

    def test_old_report_and_calendar_unknown_clear_today_candidates_without_writes(self):
        original={'generatedAt':'2026-10-29T16:00:00+08:00','asOf':'2026-10-29','instruments':[],
                  'opportunityRadar':{'through':'2026-10-29','groups':{'etf':{'candidates':[{'symbol':'FICTIONAL'}]}},'items':[{'eligible':True}]}}
        for now in ['2026-10-30T14:00:00+08:00','2027-01-04T14:00:00+08:00']:
            r=engine.mark_report_freshness(original,now=datetime.fromisoformat(now))
            self.assertEqual('historical',r['opportunityRadar']['status']);self.assertEqual([],r['opportunityRadar']['groups']['etf']['candidates'])
        self.assertTrue(original['opportunityRadar']['items'][0]['eligible'])

    def test_same_day_before_close_uses_previous_completed_session(self):
        r={'generatedAt':'2026-10-29T14:38:00+08:00','asOf':'2026-10-29','instruments':[],
           'opportunityRadar':{'through':'2026-10-28','groups':{'etf':{'candidates':[{'symbol':'FICTIONAL'}]}}}}
        viewed=engine.mark_report_freshness(r,now=datetime.fromisoformat('2026-10-29T14:40:00+08:00'))
        self.assertEqual(1,len(viewed['opportunityRadar']['groups']['etf']['candidates']))
