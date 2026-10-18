"""Conservative artifact guard; manual diff review remains necessary."""
from pathlib import Path
import subprocess
import sys
REPO = Path(__file__).resolve().parents[1]
ALLOW = {'config.example.json', 'bark.example.json'}
FORBIDDEN = {'.csv', '.json', '.html', '.db', '.sqlite', '.sqlite3', '.zip', '.bak', '.xlsx', '.xls', '.log'}


def check(all_files=False):
    args = ['git', 'ls-files', '-z'] if all_files else ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']
    files = subprocess.check_output(args, cwd=REPO).decode('utf-8').split('\0')
    bad = [f for f in files if f and f not in ALLOW and (Path(f).suffix.lower() in FORBIDDEN or any(x.lower() in ('snapshots', 'reports', 'private', 'backups') for x in Path(f).parts))]
    if bad:
        print('拒绝：发现可能包含私有数据的文件；禁止提交 CSV、快照、报告、数据库或备份。')
        return 1
    print('数据文件检查通过；仍须人工审阅 diff，无法识别所有改名或嵌入源码的隐私。')
    return 0


if __name__ == '__main__':
    raise SystemExit(check('--all' in sys.argv))
