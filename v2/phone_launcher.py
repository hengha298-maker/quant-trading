"""Windows owner panel. Device approvals and pairing secrets stay in memory."""
import queue
import os
from pathlib import Path
import threading

from tailscale_transport import PhoneShare, TailnetError, Tailscale, diagnostic_code, device_check_failure, WHOIS_CODES, BUILD_CODE

DIAGNOSTIC_PROTOCOL = 4


def format_diagnostic(codes=()):
    """Every copied result identifies this build and contains fixed codes only."""
    if codes is None:
        codes = ()
    elif isinstance(codes, str):
        codes = codes.splitlines()
    safe = tuple(dict.fromkeys(diagnostic_code(code) for code in codes))
    details = tuple(code for code in safe if code != BUILD_CODE)
    return '\n'.join((BUILD_CODE, *(details or ('diag_no_result',))))

MESSAGES = {
    'operation_failed': '请检查选定P1源码及JSON（同一已确认本地数据目录、格式有效），未建立分享。',
    'tailscale_not_installed': '请先安装官方Windows版Tailscale，再重新打开本窗口。',
    'tailscale_version_unsupported': '请使用正式版Tailscale 1.90.9或更新版本。',
    'tailscale_not_connected': '请让电脑和iPhone登录同一Tailscale账号，并保持连接。',
    'personal_identity_required': '需要同一私人账号的设备；暂不支持标签设备或分享设备。',
    'magicdns_required': '请在Tailscale管理页面开启MagicDNS和HTTPS证书。',
    'existing_serve_or_funnel': '已有Serve或Funnel配置，已拒绝启动且没有覆盖。请先自行停止旧分享。',
    'device_selection_expired': '设备状态已改变。请刷新设备列表，重新选择在线iPhone。',
    'https_start_failed': 'HTTPS未能启动。请确认Tailscale已连接，并已开启MagicDNS/HTTPS证书；不会降级为HTTP。',
    'serve_exited': 'Serve前台进程已退出（返回成功），本次分享已撤销。',
    'serve_failed': 'Serve前台进程异常退出，本次分享已撤销。',
    'serve_config_missing': 'JSON状态中的本次Serve配置消失，已撤销分享。',
    'serve_configuration_changed': 'Serve配置不再是本次唯一HTTPS代理，已撤销分享。',
    'serve_session_changed': 'Serve前台会话已改变，已撤销分享。',
    'serve_config_unknown': 'Tailscale返回了无法识别的Serve状态结构，已拒绝分享。',
    'serve_schema_unsupported': 'Serve状态结构与已核对版本不同，已拒绝分享。',
    'tailscale_status_invalid': 'Tailscale设备状态结构无法确认，已拒绝分享。',
    'tailscale_self_offline': 'Windows设备已不在线，已撤销分享。请连接电脑Tailscale后重试。',
    'tailscale_self_key_expired': 'Windows设备密钥已失效，已撤销分享。',
    'tailnet_dns_changed': '电脑的私网DNS名称已改变，旧地址及授权已撤销。',
    'device_unavailable': '原授权设备未出现在可确认的设备列表中，已撤销分享。',
    'device_identity_changed': '原设备身份或所属用户已改变，已撤销分享。',
    'device_addresses_changed': '授权设备的IP集合改变，已撤销分享；请刷新并重新核对设备。',
    'device_offline': '授权iPhone已离线，已撤销分享。请在手机连接Tailscale后重试。',
    'device_not_ios': '授权设备不再被确认为iOS设备，已撤销分享。',
    'device_key_expired': '授权iPhone密钥已失效，已撤销分享。',
    'device_tagged_or_shared': '设备带标签或属于分享节点，已撤销分享。',
    'https_setup_required': '等待前台HTTPS配置超时。请核对Tailscale证书启用状态后重试。',
}


def error_message(code):
    code = diagnostic_code(code)
    if code in WHOIS_CODES:
        explanation = '指定设备的该项whois核验未通过，已拒绝分享；复制诊断码可取得本次全部阻断条件。'
    elif code.startswith('cli_'):
        explanation = 'Tailscale CLI检查失败，已拒绝分享。'
    else:
        explanation = MESSAGES.get(code, '操作未完成，分享未保持。请复制固定诊断码。')
    return f'[{code}] {explanation} 文件选择仍保留，可直接重试。'


def refreshed_choice(previous, devices):
    """Retain the explicit choice only when all approved identity fields match."""
    if previous is not None:
        for label, current in devices.items():
            if device_check_failure(previous, [current]) is None:
                return label
    return ''


def connection_message(codes):
    explanations = {
        'check_whois_ok': '电脑已成功复核指定iPhone身份。',
        'check_whois_failed': '电脑的whois复核失败；后续固定码说明原因。',
        'check_local_https_ok': 'Windows访问自身HTTPS及应用403正常；不代表手机方向已通。',
        'phone_request_not_observed': '仅本次短窗口未记录候选请求；请同时参考history_本次分享摘要，不能据此判断网络不通。',
        'phone_candidate_arrived': '选定IP的候选请求已到应用，仍需身份核验。',
        'phone_identity_verified': '请求已通过指定设备完整身份核验。',
        'phone_page_sent': '配对页已写入Serve，不证明Safari已收到；需查返回路径及手机VPN/代理。',
        'phone_identity_rejected': '到达应用的候选请求未通过设备身份核验。',
        'phone_whois_timeout': '请求到达应用，但whois超时。',
        'phone_proxy_headers_rejected': '候选请求的代理头或安全入口不匹配。',
        'check_cancelled': '诊断已随停止分享取消。',
        'check_local_dns_failed': 'Windows本机域名解析失败。',
        'check_local_dns_mismatch': 'Windows解析结果不属于本机Tailscale地址，已停止探测。',
        'check_local_tcp_failed': 'Windows访问自身8443失败；还不能判断手机方向。',
        'check_local_tls_failed': 'Windows本机TLS握手或证书验证失败，没有降级。',
        'check_local_http_failed': '本机TLS后的HTTP结果不符合应用预期。',
    }
    safe = tuple(diagnostic_code(code) for code in codes)
    priority = ('check_cancelled', 'phone_whois_timeout', 'phone_identity_rejected',
                'phone_proxy_headers_rejected', 'phone_page_sent', 'phone_identity_verified',
                'phone_request_not_observed', 'phone_candidate_arrived')
    local = next((code for code in safe if code.startswith('check_local_')), None)
    identity = next((code for code in safe if code in WHOIS_CODES), None)
    phone = next((code for code in priority if code in safe), None)
    history = next((code for code in safe if code.startswith('history_')), None)
    selected = tuple(dict.fromkeys(code for code in (identity or local, phone or history) if code))
    for code in WHOIS_CODES:
        explanations[code] = 'whois具体身份条件未通过，已保持拒绝；完整阻断条件可一次复制。'
    return '\n'.join(f'[{code}] {explanations.get(code, "对应检查结果已记录。")}' for code in selected) + '\n完整固定码可复制；文件选择未变。'


def main(*, real=False):
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError:
        print('无法打开控制窗口；请查看PHONE-HTTPS.md。'); return 1
    app = tk.Tk(); app.title(('养鹅计划 · iPhone真实持仓只读' if real else '养鹅计划 · iPhone安全连接 · 仅虚构测试') + ' · ' + BUILD_CODE)
    app.geometry('760x580')
    events = queue.Queue(); share = None; devices = {}; busy = False; closed = False; checking = False
    device = tk.StringVar(); scenario = tk.StringVar(value='正常历史快照')
    status = tk.StringVar(value='先连接两端Tailscale，再刷新并选择本次唯一授权的iPhone。')
    url = tk.StringVar(value='尚未启动'); code = tk.StringVar(value='未生成')
    diagnostic = tk.StringVar(value=format_diagnostic())
    chosen = {'code': None, 'snapshot': None, 'markets': []}
    scenarios = {'正常历史快照': 'normal', '缺少行情': 'missing', '数据过期': 'stale'}
    body = ttk.Frame(app, padding=22); body.pack(fill='both', expand=True)
    ttk.Label(body, text='iPhone私网HTTPS · 真实持仓只读' if real else 'iPhone私网HTTPS · 仅虚构数据', font=('Microsoft YaHei', 17, 'bold')).pack(anchor='w')
    ttk.Label(body, text=('仅分享明确选择的P1文件，不修改、不上传；行情资格沿用P1。\n' if real else '仅虚构测试；真实文件入口关闭。\n') + '仅Tailscale私网HTTPS。配对码5分钟一次性有效，会话最长15分钟。', justify='left').pack(anchor='w', pady=12)
    picker = ttk.Combobox(body, textvariable=device, state='readonly', width=80); picker.pack(anchor='w', fill='x')
    cases = ttk.Combobox(body, textvariable=scenario, values=list(scenarios), state='readonly');
    if not real: cases.pack(anchor='w', pady=10)
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
        diagnostic.set(format_diagnostic())
        for button in buttons: button.configure(state='disabled')
        status.set('正在检查私网状态，请稍候。')
        def run():
            try: events.put((task, action(), None))
            except TailnetError as error: events.put((task, None, error.code))
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
        if real and (not chosen['code'] or not chosen['snapshot']):
            status.set('请先选择P1源码和已有持仓快照JSON；行情可留空。'); return
        if not messagebox.askyesno('本次设备授权', ('仅允许下面这台设备只读查看选定真实持仓：\n' if real else '仅允许下面这台设备访问虚构测试：\n') + selected.name + '\n' + ', '.join(selected.ips) + '\n请与iPhone的Tailscale设备信息核对。', parent=app): return
        folder = chosen['code'] if real else filedialog.askdirectory(parent=app, title='选择已验收P1源码目录或其中p1目录，不是持仓目录')
        if not folder: return
        selected_scenario = scenarios[scenario.get()]
        def launch():
            nonlocal share
            try:
                share = PhoneShare()
                if real:
                    return share.start(folder, selected, snapshot_file=chosen['snapshot'], market_files=list(chosen['markets']))
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

    def revoke():
        work('revoke', lambda: True if share is None else share.stop())

    def copy_url():
        if share and share.server:
            app.clipboard_clear(); app.clipboard_append(url.get())
            status.set('已复制不含凭据的地址；可发送到自己的iPhone，用Safari打开。配对码另在本窗口查看。')

    def copy_diagnostic():
        current = format_diagnostic(diagnostic.get())
        diagnostic.set(current)
        # Always replace the clipboard, even before the first result. Leaving
        # an old result there would make an older process look like this build.
        app.clipboard_clear(); app.clipboard_append(current)

    def check_connection():
        nonlocal checking
        if checking or busy or not share or not share.server:
            return
        checking = True; owner = share
        diagnostic.set(format_diagnostic(('diag_check_in_progress',)))
        status.set('正在自动检查whois、本机DNS/TCP/HTTPS。请在iPhone Safari刷新一次；停止按钮仍可使用。')
        def run():
            try: result = owner.check_connection()
            except Exception: result = ('transport_internal_error',)
            events.put(('connection', (owner, result), None))
        threading.Thread(target=run, daemon=True).start()

    if real:
        initial = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'GooseResearchP1'
        selections = ttk.Frame(body); selections.pack(anchor='w', fill='x', before=row)
        labels = {key: tk.StringVar(value='尚未选择' if key != 'markets' else '可留空；不会补演示行情') for key in chosen}
        def select(key):
            if share and share.server:
                status.set('请先停止分享，再更换选定文件。'); return
            if key == 'code':
                value = filedialog.askdirectory(parent=app, title='选择已验收PR #3源码目录或其中p1目录（不是数据目录）')
            elif key == 'snapshot':
                value = filedialog.askopenfilename(parent=app, title='选择P1 snapshots持仓快照JSON', initialdir=str(initial / 'snapshots'), filetypes=[('P1 JSON', '*.json')])
            else:
                value = filedialog.askopenfilenames(parent=app, title='选择market行情JSON；每个标的只选一份日线，可多选', initialdir=str(initial / 'market'), filetypes=[('P1 JSON', '*.json')])
            if value:
                chosen[key] = list(value) if key == 'markets' else value
                labels[key].set(f'已选 {len(value)} 份行情' if key == 'markets' else Path(value).name)
        for key, title in [('code', '1. 选择P1源码'), ('snapshot', '2. 选择持仓快照'), ('markets', '3. 选择行情（可多选）')]:
            line = ttk.Frame(selections); line.pack(anchor='w', fill='x', pady=3)
            button = ttk.Button(line, text=title, command=lambda key=key: select(key)); button.pack(side='left'); buttons.append(button)
            ttk.Label(line, textvariable=labels[key], wraplength=480).pack(side='left', padx=10)
        def clear_markets():
            if not (share and share.server):
                chosen['markets'] = []; labels['markets'].set('未选行情；不会补演示价格')
        button = ttk.Button(selections, text='清空行情选择', command=clear_markets); button.pack(anchor='w'); buttons.append(button)
    actions = [('刷新设备', refresh), ('启动分享', start), ('更新配对码', rotate), ('撤销授权', revoke), ('停止分享', stop)] if real else [('刷新设备', refresh), ('启动虚构HTTPS', start), ('撤销会话 / 新配对码', rotate), ('停止分享', stop)]
    for title, callback in actions:
        button = ttk.Button(row, text=title, command=callback); button.pack(side='left', padx=(0, 8)); buttons.append(button)
    ttk.Button(body, text='复制手机地址', command=copy_url).pack(anchor='w')
    ttk.Button(body, text='复制诊断码（仅固定代码）', command=copy_diagnostic).pack(anchor='w')
    ttk.Button(body, text='检查手机连接（同时刷新Safari）', command=check_connection).pack(anchor='w')
    ttk.Label(body, text='撤销授权、停止或关闭窗口会清空会话及内存数据、关闭本次Serve。\n更新配对码也会撤销旧会话。手机不保存持仓；请勿分享配对码。' if real else '停止或关闭本窗口会清空会话、关闭本机接口及前台Serve进程。\n不要向他人发送配对码；手机四页一直标记“仅虚构测试”。', justify='left').pack(anchor='w', pady=12)

    def pump():
        nonlocal busy, devices, checking
        if closed: return
        try:
            while True:
                task, result, error = events.get_nowait()
                if task == 'connection':
                    checking = False
                    owner, codes = result
                    if share is owner and owner.server and not busy:
                        safe = tuple(diagnostic_code(code) for code in codes)
                        diagnostic.set(format_diagnostic(safe)); status.set(connection_message(safe))
                    continue
                busy = False
                for button in buttons: button.configure(state='normal')
                if error or isinstance(result, tuple) and result[0] == 'error':
                    key = result[1] if isinstance(result, tuple) else error
                    details = getattr(share, 'identity_report', ()) if task == 'start' else ()
                    safe = tuple(dict.fromkeys(diagnostic_code(item) for item in (key, *details)))
                    diagnostic.set(format_diagnostic(safe)); status.set(error_message(key))
                    if task == 'start': url.set('已停止'); code.set('已撤销')
                elif task == 'refresh':
                    previous = devices.get(device.get())
                    devices = {f'{p.name} · {p.ips[0]} · {p.node_id[-6:]}': p for p in result}
                    picker.configure(values=list(devices)); device.set(refreshed_choice(previous, devices))
                    status.set('请选择并核对本次授权iPhone。' if devices else '没有同账号在线iPhone。请在手机打开Tailscale并连接后刷新。')
                elif task == 'start':
                    if share and share.server and share.gate.ready:
                        diagnostic.set(format_diagnostic())
                        url.set(result); code.set(share.server.access.pair_code)
                        status.set(('真实持仓只读分享已启动。' if real else '虚构HTTPS已启动。') + '用指定iPhone的Safari打开地址，输入配对码。')
                    else:
                        url.set('已停止'); code.set('已撤销')
                        status.set('连接状态已改变，未保持分享。请重新检查Tailscale和设备状态。')
                elif task in ('stop', 'revoke'):
                    url.set('已停止'); code.set('已撤销')
                    status.set('已撤销授权、清空内存数据并确认Serve关闭。' if result else '数据接口及会话已关闭，但未能确认Serve状态。请退出Windows Tailscale，并查看PHONE-HTTPS.md。')
        except queue.Empty:
            pass
        if not busy and share and share.error:
            code.set('已撤销'); url.set('已停止')
            details = getattr(share, 'identity_report', ())
            safe = tuple(dict.fromkeys(diagnostic_code(item) for item in (share.error, *details)))
            diagnostic.set(format_diagnostic(safe))
            status.set(error_message(share.error) + ('' if share.stop_confirmed else ' [stop_unconfirmed] 请退出Windows Tailscale以确认断开。'))
        app.after(150, pump)

    def close():
        nonlocal closed
        if busy:
            status.set('操作进行中，请等待完成后关闭窗口。'); return
        closed = True
        if share: share.stop()
        app.destroy()
    app.protocol('WM_DELETE_WINDOW', close); app.after(150, pump)
    app.update_idletasks(); app.geometry(f'760x{max(580, app.winfo_reqheight()+20)}')
    try: app.mainloop()
    finally:
        if share: share.stop()
    return 0


if __name__ == '__main__': raise SystemExit(main())
