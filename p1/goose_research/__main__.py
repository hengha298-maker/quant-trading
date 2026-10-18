import argparse
import sys
import csv
from .storage import initialize, root
from .holdings import FIELDS, import_csv
from .market import collect
from .report import generate


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description='养鹅 P1：手动、只读、离线报告；不使用 V1 配置')
    parser.add_argument('--data-dir', required=True, help='仓库外、非云同步的本地私有目录')
    subs = parser.add_subparsers(dest='command', required=True)
    init = subs.add_parser('init')
    init.add_argument('--confirm-local-storage', action='store_true')
    subs.add_parser('template')
    imp = subs.add_parser('import-csv')
    imp.add_argument('csv')
    market = subs.add_parser('collect')
    market.add_argument('symbol')
    market.add_argument('--interval', choices=['daily', 'minute'], default='daily')
    report = subs.add_parser('report')
    report.add_argument('snapshot')
    report.add_argument('--market', action='append', default=[])
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            out = initialize(args.data_dir, args.confirm_local_storage)
        elif args.command == 'template':
            out = root(args.data_dir) / 'holdings-template.csv'
            with out.open('x', encoding='utf-8-sig', newline='') as f:
                w = csv.writer(f)
                w.writerow(FIELDS)
                # Explicitly fictional and partial; blank cost means unknown.
                w.writerow(['DEMO', '华宝示例', '2026-01-05', 'partial', '', '600900.SH', 'stock', '100', '100', ''])
                w.writerow(['DEMO', '华宝示例', '2026-01-05', 'partial', '', '515450.SH', 'etf', '100', '100', ''])
        elif args.command == 'import-csv':
            out = import_csv(args.csv, args.data_dir)
        elif args.command == 'collect':
            out = collect(args.data_dir, args.symbol, args.interval)
        else:
            out = generate(args.data_dir, args.snapshot, args.market)
        print(out)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print('操作失败：' + type(exc).__name__ + '；请检查格式、路径和本地文件权限。历史快照不会被覆盖。')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
