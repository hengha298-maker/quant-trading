"""Windows-friendly control panel; selects SOURCE CODE, never personal files."""
import threading
import webbrowser
from readonly_server import create_server


def main():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print('无法打开控制窗口；请使用READONLY.md中的标准库命令。');return 1
    app=tk.Tk();app.title('养鹅计划 · 只读模式虚构测试');app.geometry('610x450')
    app.configure(bg='#f2f5f0')
    server=None;thread=None
    label=tk.StringVar(value='尚未启动；真实持仓入口始终关闭。')
    code=tk.StringVar(value='未生成')
    scenarios={'正常历史快照':'normal','缺少行情':'missing','数据过期':'stale'}
    scenario=tk.StringVar(value='正常历史快照')
    tk.Label(app,text='real_readonly · 仅虚构测试',font=('Microsoft YaHei',18,'bold'),bg='#f2f5f0').pack(anchor='w',padx=24,pady=(22,8))
    tk.Label(app,text='仅本机HTTP + 一次性配对；不支持真实文件、手机、LAN或公网。\n选择PR #3源码目录，不要选择持仓数据目录。',justify='left',wraplength=560,bg='#f2f5f0').pack(anchor='w',padx=24)
    selector=tk.OptionMenu(app,scenario,*scenarios);selector.pack(anchor='w',padx=24,pady=10)
    tk.Label(app,text='可选择三种虚构情景；停止后可切换并重新启动。',bg='#f2f5f0').pack(anchor='w',padx=24)
    tk.Label(app,textvariable=label,wraplength=560,justify='left',bg='#f2f5f0').pack(anchor='w',padx=24,pady=12)
    tk.Label(app,textvariable=code,font=('Consolas',24),bg='#f2f5f0').pack(anchor='w',padx=24)
    def start():
        nonlocal server,thread
        if server:return
        folder=filedialog.askdirectory(parent=app,title='选择PR #3 f6c117f源码目录或其p1文件夹（不是持仓目录）')
        if not folder:return
        try:server=create_server(folder,scenario=scenarios[scenario.get()])
        except (ValueError,OSError):
            messagebox.showerror('未启动','P1核心版本不匹配或9876端口被占用；不会自动下载或安装。请参考READONLY.md。',parent=app);return
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        code.set(server.access.pair_code);label.set(server.origin+'/readonly/\n仅虚构测试；配对码5分钟有效，用一次失效。')
        selector.configure(state='disabled');webbrowser.open(server.origin+'/readonly/')
    def revoke():
        if server:code.set(server.access.rotate());label.set(server.origin+'/readonly/\n已撤销旧会话；请用新的配对码重新进入。')
    def stop():
        nonlocal server,thread
        if server:
            server.access.close();server.shutdown();server.server_close();thread.join(timeout=3)
            server=None;thread=None
        code.set('已停止');label.set('连接已关闭，会话已撤销；未修改任何P1数据或V1.1。');selector.configure(state='normal')
    row=tk.Frame(app,bg='#f2f5f0');row.pack(anchor='w',padx=24,pady=14)
    for title,callback in [('启动虚构测试',start),('撤销会话 / 更新配对码',revoke),('停止分享',stop)]:
        tk.Button(row,text=title,command=callback,padx=8,pady=7).pack(side='left',padx=(0,8))
    def close():stop();app.destroy()
    app.protocol('WM_DELETE_WINDOW',close)
    app.update_idletasks();app.geometry(f'610x{max(450,app.winfo_reqheight()+20)}')
    try:app.mainloop()
    finally:
        if server:stop()
    return 0


if __name__=='__main__':raise SystemExit(main())
