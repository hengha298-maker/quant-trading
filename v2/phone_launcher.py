"""Windows owner panel. Device approvals and pairing secrets stay in memory."""
import queue
import threading

from tailscale_transport import PhoneShare, TailnetError, Tailscale

MESSAGES = {
    'tailscale_not_installed': '请先安装官方Windows版Tailscale，再重新打开本窗口。',
    'tailscale_version_unsupported': '请使用正式版Tailscale 1.90.9或更新版本。',
    'tailscale_not_connected': '请让电脑和iPhone登录同一Tailscale账号，并保持连接。',
    'personal_identity_required': '需要同一私人账号的设备；暂不支持标签设备或分享设备。',
    'magicdns_required': '请在Tailscale管理页面开启MagicDNS和HTTPS证书。',
    'existing_serve_or_funnel': '已有Serve或Funnel配置，已拒绝启动且没有覆盖。请先自行停止旧分享。',
    'device_selection_expired': '设备状态已改变。请刷新设备列表，重新选择在线iPhone。',
    'https_start_failed': 'HTTPS未能启动。请确认Tailscale已连接，并已开启MagicDNS/HTTPS证书；不会降级为HTTP。',
}


def main():
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print('无法打开控制窗口；请查看PHONE-HTTPS.md。'); return 1
    app = tk.Tk(); app.title('养鹅计划 · iPhone安全连接 · 仅虚构测试')
    app.geometry('730x580')
    events = queue.Queue(); share = None; devices = {}; busy = False; closed = False
    device = tk.StringVar(); scenario = tk.StringVar(value='正常历史快照')
    status = tk.StringVar(value='先连接两端Tailscale，再刷新并选择本次唯一授权的iPhone。')
    url = tk.StringVar(value='尚未启动'); code = tk.StringVar(value='未生成')
    scenarios = {'正常历史快照': 'normal', '缺少行情': 'missing', '数据过期': 'stale'}
    body = ttk.Frame(app, padding=22); body.pack(fill='both', expand=True)
    ttk.Label(body, text='iPhone私网HTTPS · 仅虚构数据', font=('Microsoft YaHei', 17, 'bold')).pack(anchor='w')
    ttk.Label(body, text='仅Tailscale Serve，不使用Funnel；不改防火墙、路由器或常驻任务。\n真实持仓入口保持关闭。配对码5分钟一次性有效，会话最长15分钟。', justify='left').pack(anchor='w', pady=12)
    picker = ttk.Combobox(body, textvariable=device, state='readonly', width=80); picker.pack(anchor='w', fill='x')
    cases = ttk.Combobox(body, textvariable=scenario, values=list(scenarios), state='readonly'); cases.pack(anchor='w', pady=10)
    ttk.Label(body, textvariable=status, wraplength=670, justify='left').pack(anchor='w', pady=12)
    ttk.Label(body, text='手机Safari访问地址（不含配对码）').pack(anchor='w')
    ttk.Entry(body, textvariable=url, state='readonly', width=85).pack(anchor='w', fill='x', pady=6)
    ttk.Label(body, textvariable=code, font=('Consolas', 25)).pack(anchor='w', pady=10)
    row = ttk.Frame(body); row.pack(anchor='w', pady=12)
    buttons = []

    def work(task, action):
        nonlocal busy
        if busy or closed: return
        busy = True
        for button in buttons: button.configure(state='disabled')
        status.set('正在检查私网状态，请稍候；只使用虚构数据。')
        def run():
            try: events.put((task, action(), None))
            except (ValueError, OSError): events.put((task, None, 'operation_failed'))
        threading.Thread(target=run, daemon=True).start()

    def refresh():
        if share and share.server:
            status.set('分享中不能更换设备。请先停止分享。'); return
        def inspect():
            try: return Tailscale().inspect()[1]
            except TailnetError as error: return ('error', str(error))
        work('refresh', inspect)

    def start():
        nonlocal share
        selected = devices.get(device.get())
        if selected is None:
            status.set('请先刷新并选择在线iPhone；不会自动授权任何设备。'); return
        if share and share.server: return
        if not messagebox.askyesno('本次设备授权', '仅允许下面这台设备访问虚构测试：\n' + selected.name + '\n' + ', '.join(selected.ips) + '\n请与iPhone的Tailscale设备信息核对。', parent=app): return
        folder = filedialog.askdirectory(parent=app, title='选择已验收P1源码目录或其中p1目录，不是持仓目录')
        if not folder: return
        selected_scenario = scenarios[scenario.get()]
        def launch():
            nonlocal share
            try:
                share = PhoneShare()
                return share.start(folder, selected, scenario=selected_scenario)
            except TailnetError as error: return ('error', str(error))
        work('start', launch)

    def rotate():
        if share and share.server:
            try:
                code.set(share.rotate()); status.set('旧会话已撤销。请在同一授权iPhone输入新配对码。')
            except ValueError: status.set('连接已关闭，请重新启动。')

    def stop():
        work('stop', lambda: True if share is None else share.stop())

    def copy_url():
        if share and share.server:
            app.clipboard_clear(); app.clipboard_append(url.get())
            status.set('已复制不含凭据的地址；可发送到自己的iPhone，用Safari打开。配对码另在本窗口查看。')

    for title, callback in [('刷新设备', refresh), ('启动虚构HTTPS', start), ('撤销会话 / 新配对码', rotate), ('停止分享', stop)]:
        button = ttk.Button(row, text=title, command=callback); button.pack(side='left', padx=(0, 8)); buttons.append(button)
    ttk.Button(body, text='复制手机地址', command=copy_url).pack(anchor='w')
    ttk.Label(body, text='停止或关闭本窗口会清空会话、关闭本机接口及前台Serve进程。\n不要向他人发送配对码；手机四页一直标记“仅虚构测试”。', justify='left').pack(anchor='w', pady=12)

    def pump():
        nonlocal busy, devices
        if closed: return
        try:
            while True:
                task, result, error = events.get_nowait(); busy = False
                for button in buttons: button.configure(state='normal')
                if error or isinstance(result, tuple) and result[0] == 'error':
                    key = result[1] if isinstance(result, tuple) else error
                    status.set(MESSAGES.get(key, '操作未完成；没有读取真实数据。请检查Tailscale连接及P1源码版本。'))
                elif task == 'refresh':
                    devices = {f'{p.name} · {p.ips[0]} · {p.node_id[-6:]}': p for p in result}
                    picker.configure(values=list(devices)); device.set('')
                    status.set('请选择并核对本次授权iPhone。' if devices else '没有同账号在线iPhone。请在手机打开Tailscale并连接后刷新。')
                elif task == 'start':
                    if share and share.server and share.gate.ready:
                        url.set(result); code.set(share.server.access.pair_code)
                        status.set('虚构HTTPS已启动。用指定iPhone的Safari打开地址，输入配对码。')
                    else:
                        url.set('已停止'); code.set('已撤销')
                        status.set('连接状态已改变，未保持分享。请重新检查Tailscale和设备状态。')
                elif task == 'stop':
                    url.set('已停止'); code.set('已撤销')
                    status.set('已撤销会话、清空虚构数据并确认Serve关闭。' if result else '数据接口及会话已关闭，但未能确认Serve状态。请退出Windows Tailscale，并查看PHONE-HTTPS.md。')
        except queue.Empty:
            pass
        if not busy and share and share.error:
            code.set('已撤销'); url.set('已停止'); status.set(share.error + ('' if share.stop_confirmed else ' 请退出Windows Tailscale以确认断开。'))
        app.after(150, pump)

    def close():
        nonlocal closed
        if busy:
            status.set('操作进行中，请等待完成后关闭窗口。'); return
        closed = True
        if share: share.stop()
        app.destroy()
    app.protocol('WM_DELETE_WINDOW', close); app.after(150, pump)
    app.update_idletasks(); app.geometry(f'730x{max(580, app.winfo_reqheight()+20)}')
    try: app.mainloop()
    finally:
        if share: share.stop()
    return 0


if __name__ == '__main__': raise SystemExit(main())
