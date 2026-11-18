"""Regression for explicit isolated cloud collection roots; zero networking."""
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from investment import market

class CloudPathTests(unittest.TestCase):
    def test_explicit_private_root_collects_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(market, '_attempt', side_effect=AssertionError('NETWORK_FORBIDDEN')):
                value = market.collect_market([], root/'investment/work/market', data_root=root)
            self.assertEqual({}, value['quotes'])
            self.assertTrue((root/'investment/work/market/market.json').is_file())

    def test_implicit_external_root_and_nonwork_paths_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                market._output_location(root/'investment/work/market')
            for path in (root/'public/raw', root/'private/raw', root.parent/'escape/raw'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    market._output_location(path, root)
            with self.assertRaises(ValueError):
                market._output_location(root/'public/investment/work', root/'public')

    def test_link_alias_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'investment').mkdir()
            try:
                (root/'investment/work').symlink_to(root, target_is_directory=True)
            except OSError:
                self.skipTest('symlink creation unavailable on this local platform')
            with self.assertRaises(ValueError):
                market._output_location(root/'investment/work/market', root)

if __name__ == '__main__':
    unittest.main()
