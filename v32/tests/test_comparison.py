import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from goose_research.cli import synthetic_dataset
from goose_research.comparison import compare_sources


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.a=synthetic_dataset();self.b=copy.deepcopy(self.a)
        self.b['source']='SYNTHETIC alternate provider';self.b['sourceUrl']='https://example.invalid/alternate'

    def test_exact_numeric_agreement_does_not_promote_unit_or_source_certification(self):
        before=copy.deepcopy(self.a)
        result=compare_sources(self.a,self.b)
        self.assertTrue(result['numericAgreement']);self.assertEqual(result['sessionsCompared'],243)
        self.assertFalse(result['independentExchangeFeedProven']);self.assertFalse(result['unitVerificationEstablished'])
        self.assertEqual(self.a,before)

    def test_close_or_volume_mismatch_is_detected_without_price_scaling(self):
        self.b['bars'][0]['volume']+=1
        result=compare_sources(self.a,self.b)
        self.assertFalse(result['numericAgreement']);self.assertEqual(result['mismatchCount'],1)

    def test_same_provider_and_adjusted_mix_are_rejected(self):
        with self.assertRaises(ValueError):compare_sources(self.a,self.a)
        self.b['adjustment']='qfq'
        with self.assertRaises(ValueError):compare_sources(self.a,self.b)


if __name__=='__main__':unittest.main()
