"""Real pinned decoder/API, synthetic encoded bars, NO network or live claims.

These two bitstreams were constructed from the 1.19.1 decoder's grammar, NOT
downloaded from Sina. They encode the same three deliberately fictional bars.
1479: precision=3, initial date, OHLC/volume delta widths=12 bits.
3466: default price precision=6 (P() => /100), explicit initial date,
OHLC widths=9, volume/amount widths=15, then the explicit end marker.
Each alphabet index carries six LSB-first bits, not Base64-encoded bytes.
"""
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goose_research import probe_sina as probe, sina

# FICTIONAL UNIT-TEST FIXTURES. Never use them as observations or fallback data.
FIXTURES = (
    ('KLC_KL_sh515450', 1479, 'HX/DRCDAg/kMRkBkByAkBAAAkBkByAKAAAAkBkByA2/'),
    ('KLC_K2_sh515450', 3466, 'K2/HCMRCDcjCUQBIDAAAAAFogCoAAAAAAKQBFw+/AAYA'),
)
EXPECTED = [
    dict(time='2025-01-02', open='1.1', high='1.2', low='1', close='1.15', volume='100'),
    dict(time='2025-01-03', open='1.15', high='1.25', low='1.05', close='1.2', volume='110'),
    dict(time='2025-01-06', open='1.2', high='1.3', low='1.1', close='1.25', volume='100'),
]
NOW = datetime(2025, 1, 6, 9, tzinfo=timezone.utc)


class Response:
    status = 200
    def __init__(self, wrapper, payload):
        # Whitespace deliberately retained to check the original body is used.
        self.body = (' \nvar ' + wrapper + '="' + payload + '";\n').encode()
        self.headers = {'Content-Length': str(len(self.body))}
    def __enter__(self): return self
    def __exit__(self, *_args): pass
    def geturl(self): return sina.URL
    def read(self, limit): return self.body[:limit]


def installed():
    try:
        return metadata.version('akshare') == '1.19.1' and metadata.version('py-mini-racer') == '0.6.0'
    except metadata.PackageNotFoundError:
        return False


class HeaderTests(unittest.TestCase):
    def test_user_reported_k2_header_and_legacy_header_dispatch(self):
        self.assertEqual(sina._encoding_header('K2/3rPX'), (3466, 0))
        self.assertEqual(sina._encoding_header('HX/DRC'), (1479, 0))
        self.assertEqual(sina._encoding_header('K2+3rPX'), (3466, 1))


@unittest.skipUnless(installed(), 'optional pinned Sina tools absent; full dependency CI executes real decoder/API')
class PinnedDecoderTests(unittest.TestCase):
    def test_real_pinned_decoder_executes_both_families_with_known_prices_and_dates(self):
        for wrapper, codec, payload in FIXTURES:
            with self.subTest(wrapper=wrapper, codec=codec):
                decoded = sina._decode_payload(payload)
                self.assertEqual(sina._normalise_rows(decoded), EXPECTED)
                # 3466 carries amount/optional post-session fields; 1479 is
                # the older OHLCV-delta layout. They are not renamed streams.
                self.assertEqual('amount' in decoded[0], codec == 3466)

    def test_real_original_api_matches_same_unrenamed_body_for_both_families(self):
        for wrapper, codec, payload in FIXTURES:
            response = Response(wrapper, payload)
            with self.subTest(wrapper=wrapper):
                result = probe._probe_worker('2025-01-03', '2025-01-06', probe.BASES[0],
                    opener=lambda *_a, **_k: response, now=NOW)
                self.assertEqual((result['raw_count'], result['selected_count']), (3, 2))
                self.assertEqual(result['encoding'], dict(wrapper=wrapper, codec=codec, version=0, response_bytes=len(response.body)))
                self.assertEqual(result['first_bar'], EXPECTED[1])
                self.assertEqual(result['last_bar'], EXPECTED[2])
                self.assertTrue(result['api_rows_match'])
                self.assertIs(probe._validate_summary(result, '2025-01-03', '2025-01-06', probe.BASES[0]), result)

    def test_real_decoded_valid_k2_still_fails_when_not_fresh(self):
        response = Response(FIXTURES[1][0], FIXTURES[1][2])
        with self.assertRaises(probe.SinaStaleDataError):
            probe._probe_worker('2025-01-03', '2025-01-07', probe.BASES[0],
                opener=lambda *_a, **_k: response, now=NOW)

    def test_valid_header_without_valid_k2_stream_cannot_succeed(self):
        response = Response('KLC_K2_sh515450', 'K2/AAAA')
        with self.assertRaises(sina.SinaError):
            probe._probe_worker('2025-01-03', '2025-01-06', probe.BASES[0],
                opener=lambda *_a, **_k: response, now=NOW)

    def test_body_that_original_api_cannot_parse_is_not_silently_rewritten(self):
        response = Response(FIXTURES[1][0], FIXTURES[1][2])
        response.body = response.body.replace(b'="', b'= "')
        response.headers['Content-Length'] = str(len(response.body))
        with self.assertRaises(sina.SinaDecodeError):
            probe._probe_worker('2025-01-03', '2025-01-06', probe.BASES[0],
                opener=lambda *_a, **_k: response, now=NOW)


if __name__ == '__main__':
    unittest.main()
