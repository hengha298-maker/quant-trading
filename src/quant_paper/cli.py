import argparse
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from .core import BJ, Config, Store, run
from .market import AkshareProvider, Calendar


def main():
    parser = argparse.ArgumentParser(description="A股纯模拟交易：无实盘功能")
    parser.add_argument("command", choices=["run", "status"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--database", default="data/paper.sqlite3")
    args = parser.parse_args()
    Path("logs").mkdir(exist_ok=True)
    logger = logging.getLogger("quant-paper")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler("logs/paper.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    logger.addHandler(handler)
    store = None
    try:
        cfg = Config(**json.loads(Path(args.config).read_text(encoding="utf-8-sig")))
        Path(args.database).parent.mkdir(parents=True, exist_ok=True)
        store = Store(args.database, cfg)
        if args.command == "run":
            result = run(store, cfg, AkshareProvider(cfg.etf_symbol), Calendar(), datetime.now(BJ))
            logger.info("%s %s", datetime.now(BJ).isoformat(), result)
            print(json.dumps(result, ensure_ascii=False))
        print(json.dumps(store.snapshot(), ensure_ascii=False, indent=2))
        return 0
    except Exception:
        logger.exception("%s 运行失败，禁止继续下单", datetime.now(BJ).isoformat())
        print("运行失败，已停止。请查看 logs/paper.log（禁止使用旧行情重试成交）。", file=sys.stderr)
        return 1
    finally:
        if store:
            store.db.close()
        logger.removeHandler(handler)
        handler.close()


if __name__ == "__main__":
    sys.exit(main())
