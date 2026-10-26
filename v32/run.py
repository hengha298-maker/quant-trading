"""Explicit command-line research entry; never imports V1 business code."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from goose_research.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
