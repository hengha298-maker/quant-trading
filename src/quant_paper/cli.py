import argparse
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from .core import BJ, Config, Store, run
from .market import AkshareProvider, Calendar
from .notify import deliver, send_report


def main():
    parser = argparse.ArgumentParser(description="A股纯模拟交易：无实盘功能")
    parser.add_argument("command", choices=["run", "status", "notify-test"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--database", default="data/paper.sqlite3")
    parser.add_argument("--bark-config", default="bark.local.json")
    parser.add_argument("--notify", choices=["auto", "always", "never"], default="auto",
                        help="auto：跳过非交易时段正常通知；always：包含跳过；never：关闭本次通知")
    args = parser.parse_args()
    if args.command == "notify-test":
        status = deliver(args.bark_config, "模拟交易通知测试",
                         f"{datetime.now(BJ):%Y-%m-%d %H:%M:%S} 北京时间\nBark 配置测试成功；此命令不会读取行情、创建账户或触发模拟交易。")
        print("Bark 通知：" + status)
        return 0 if status == "成功" else 1
    logger = logging.getLogger("quant-paper")
    logger.setLevel(logging.INFO)
    handler = None
    store = None
    now = datetime.now(BJ)
    result = {}
    try:
        Path("logs").mkdir(exist_ok=True)
        handler = RotatingFileHandler("logs/paper.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        logger.addHandler(handler)
        cfg = Config(**json.loads(Path(args.config).read_text(encoding="utf-8-sig")))
        Path(args.database).parent.mkdir(parents=True, exist_ok=True)
        store = Store(args.database, cfg)
        if args.command == "run":
            result = run(store, cfg, AkshareProvider(cfg.etf_symbol), Calendar(), now, outcomes=result)
            logger.info("%s %s", datetime.now(BJ).isoformat(), result)
            send_report(args.bark_config, logger, now, result, store, args.notify)
            print(json.dumps(result, ensure_ascii=False))
        print(json.dumps(store.snapshot(), ensure_ascii=False, indent=2))
        return 0
    except Exception:
        if args.command == "run":
            send_report(args.bark_config, logger, now, result, store, args.notify, error=True)
        logger.exception("%s 运行失败，禁止继续下单", datetime.now(BJ).isoformat())
        print("运行失败，已停止。请查看 logs/paper.log（禁止使用旧行情重试成交）。", file=sys.stderr)
        return 1
    finally:
        if store:
            store.db.close()
        if handler:
            logger.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    sys.exit(main())
