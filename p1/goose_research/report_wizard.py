"""Local file picker for existing snapshots; no discovery, imports or network."""
from pathlib import Path
import os
import sys
import webbrowser
from .storage import local_path, root
from .report import generate


def create_report(snapshot_file, market_files):
    snapshot = local_path(snapshot_file)
    if snapshot.parent.name != 'snapshots':
        raise ValueError('请选择P1 snapshots目录中的快照')
    base = root(snapshot.parent.parent)
    return generate(base, snapshot, list(market_files))


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print('无法打开本地文件选择窗口；可使用README中的report命令。')
        return 1
    app = tk.Tk()
    app.title('养鹅计划 · 生成本地资产报告')
    app.geometry('560x460')
    app.minsize(480, 460)
    app.configure(bg='#f4f6f2')
    chosen = {'snapshot': None, 'markets': []}
    snapshot_label = tk.StringVar(value='尚未选择 · 必选')
    market_label = tk.StringVar(value='尚未选择 · 可留空，报告会提示缺少行情')
    initial = str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'GooseResearchP1')
    tk.Label(app, text='生成我的资产报告', font=('Microsoft YaHei', 19, 'bold'), bg='#f4f6f2', fg='#183d36').pack(anchor='w', padx=25, pady=(24, 6))
    tk.Label(app, text='只读选定文件，不联网、不修改快照。生成后用默认浏览器打开。', wraplength=500,
             justify='left', bg='#f4f6f2', fg='#63746e').pack(anchor='w', padx=25)

    def select_snapshot():
        value = filedialog.askopenfilename(parent=app, title='选择已有P1持仓快照（snapshots目录）',
            initialdir=str(Path(initial) / 'snapshots'), filetypes=[('P1 JSON快照', '*.json')])
        if value:
            chosen['snapshot'] = value
            snapshot_label.set(Path(value).name)

    def select_markets():
        values = filedialog.askopenfilenames(parent=app, title='选择行情记录；同标的只选一份日线，可多选',
            initialdir=str(Path(initial) / 'market'), filetypes=[('P1 JSON行情', '*.json')])
        if values:
            chosen['markets'] = list(values)
            market_label.set(f'已选 {len(values)} 份行情（未自动挑选最新记录）')

    def clear_markets():
        chosen['markets'] = []
        market_label.set('未选择行情 · 报告将显示缺少行情')

    for title, callback, variable in [('1. 选择持仓快照', select_snapshot, snapshot_label), ('2. 选择行情（可多选）', select_markets, market_label)]:
        row = tk.Frame(app, bg='#f4f6f2')
        row.pack(fill='x', padx=25, pady=(18, 0))
        tk.Button(row, text=title, command=callback, padx=10, pady=5).pack(anchor='w')
        tk.Label(row, textvariable=variable, bg='#f4f6f2', fg='#63746e', wraplength=500, justify='left').pack(anchor='w', pady=(4, 0))
    tk.Button(app, text='清空行情选择', command=clear_markets, relief='flat', bg='#f4f6f2', fg='#225c48').pack(anchor='w', padx=25, pady=(4, 6))

    def create():
        if chosen['snapshot'] is None:
            messagebox.showinfo('尚未选择快照', '先选择已有的P1持仓快照；本入口不会导入或推算持仓。', parent=app)
            return
        try:
            dest = create_report(chosen['snapshot'], chosen['markets'])
        except (ValueError, OSError, KeyError, TypeError):
            messagebox.showerror('未能生成报告', '请检查选定文件：必须属于同一个已确认的P1本地数据目录，格式及行情须通过校验。历史快照不会被覆盖。', parent=app)
            return
        try:
            opened = webbrowser.open(dest.as_uri())
        except OSError:
            opened = False
        if not opened:
            messagebox.showinfo('报告已生成', '请在选定数据目录的reports文件夹中，用浏览器打开新生成的HTML报告。', parent=app)

    tk.Button(app, text='生成并打开报告', command=create, bg='#225c48', fg='white', padx=18, pady=8).pack(anchor='w', padx=25)
    app.update_idletasks()
    required_height = max(460, app.winfo_reqheight() + 20)
    app.minsize(480, required_height)
    app.geometry(f'560x{required_height}')
    app.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
