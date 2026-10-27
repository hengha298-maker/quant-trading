"""Run with -I -B without installing or modifying any Python environment."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent / 'v32'))
from goose_intraday.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
