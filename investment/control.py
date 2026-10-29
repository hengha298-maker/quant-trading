"""A single local control window with a persistent, explicitly revocable read link."""
from pathlib import Path
import threading, tkinter as tk
import json,re,subprocess
from tkinter import ttk,messagebox
import webbrowser
def private_host():
 """Read current local identity at runtime; never store it in source or logs."""
 try:
  result=subprocess.run([r'C:\Program Files\Tailscale\tailscale.exe','status','--json'],capture_output=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  if result.returncode or len(result.stdout)>4*1024*1024:return None
  status=json.loads(result.stdout)
  if type(status) is not dict or type(status.get('Self')) is not dict:return None
  name=status['Self'].get('DNSName')
  if type(name) is not str:return None
  name=name.rstrip('.')
  if len(name)>253 or any(not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in name.split('.')):return None
  if status.get('BackendState')!='Running' or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',name) or not name.endswith('.ts.net'):return None
  return name+':10001'
 except (OSError,ValueError,subprocess.TimeoutExpired):return None

def open_control(root, port=9394):
 from .server import make_server
 from .access import load_or_create_access_key, rotate_access_key
 from .serve_bridge import ensure_private_route, close_owned_route, check_private_route
 root = Path(root)
 phone_host = private_host()
 try:
  server = make_server(root, port=port,
      allowed_hosts=[phone_host] if phone_host else [],
      allowed_origins=['https://'+phone_host] if phone_host else [],
      access_key=load_or_create_access_key(root))
 except (OSError, ValueError):
  error_window = None
  try:
   error_window = tk.Tk(); error_window.withdraw()
   messagebox.showerror('养鹅研究', '无法启动：端口被占用，或私有链接存储无法核验。未关闭其他程序、未覆盖密钥或数据。')
  finally:
   if error_window is not None: error_window.destroy()
  return
 window = None
 status = None
 serving = False
 server_closed = False
 destroyed = False
 route_closed = False
 bridge = {'ready':False, 'status':'unavailable', 'code':'PHONE_TAILSCALE_OFFLINE', 'ownedProcess':None}

 def cleanup(*, final=False):
  nonlocal server_closed, destroyed, route_closed
  # Each resource gets its own cleanup opportunity even if another fails.
  # Set flags before calls so a window callback + finally cannot double-close.
  if not server_closed:
   server_closed = True
   try:
    if serving: server.shutdown()
   except Exception:
    pass
   finally:
    try: server.server_close()
    except Exception: pass
  if not route_closed:
   try: close_owned_route(bridge)
   except Exception: pass
   route_closed = bridge.get('ownedProcess') is None
  if not route_closed and not final:
   if status is not None: status.set('本地页面已关闭；临时分享进程停止未确认，请再次关闭重试。没有重置其他分享配置。')
   return False
  if window is not None and not destroyed:
   destroyed = True
   try: window.destroy()
   except Exception: pass
  return route_closed

 try:
  threading.Thread(target=server.serve_forever, daemon=True).start()
  serving = True
  if phone_host: bridge = ensure_private_route(phone_host, port=port)
  server.phone_route_status = {k:bridge[k] for k in ['ready', 'status', 'code']}
  window = tk.Tk(); window.title('养鹅计划 · 投资研究控制台'); window.geometry('650x470')
  frame = ttk.Frame(window, padding=24); frame.pack(fill='both', expand=True)
  ttk.Label(frame, text='养鹅投资研究', font=('Microsoft YaHei UI',21,'bold')).pack(anchor='w')
  ttk.Label(frame, text='真实持仓导入 · 当日行情核验 · 中文报告\n原V1/V2/P1和手机离线数据均保留；本程序不下单、不推送。', wraplength=590).pack(anchor='w',pady=12)
  key = server.access_key
  def desktop(): return f'http://127.0.0.1:{port}/investment/#key={server.access_key}'
  def phone(): return f'https://{phone_host}/investment/#key={server.access_key}' if phone_host else None
  ttk.Button(frame,text='打开投资分析 / 导入最新持仓',command=lambda:webbrowser.open(desktop())).pack(fill='x',ipady=7)
  status = tk.StringVar(value='已启动。打开页面后可查看新分析；手机分享状态：'+bridge['status']+'。')
  def copy_phone():
   if phone_host:
    # A borrowed foreground route can disappear when its original controller
    # closes. Recheck read-only; never spawn another child from this button.
    checked = check_private_route(phone_host, port=port)
    for field in ('ready','status','code'): bridge[field] = checked[field]
    server.phone_route_status = {k:bridge[k] for k in ('ready','status','code')}
   if phone() is None or not bridge['ready'] or server_closed:
    status.set('手机分享未就绪，请检查Tailscale后重开此入口。诊断：'+bridge['code']); return
   window.clipboard_clear(); window.clipboard_append(phone())
   status.set('私有手机链接已复制：仅发给你自己，在Safari打开。重启后可继续使用；关闭窗口时无法访问，不要转发他人。')
  ttk.Button(frame,text='复制 iPhone 私有报告地址',command=copy_phone).pack(fill='x',ipady=7,pady=12)
  def reset_phone():
   try: server.access_key = rotate_access_key(root)
   except (OSError,ValueError): status.set('链接重置失败，原有效链接和持仓均保留。'); return
   status.set('旧手机链接已撤销，请重新复制一次。以后正常重启可继续使用新链接。')
   webbrowser.open(desktop())
  ttk.Button(frame,text='重置手机链接（撤销旧链接）',command=reset_phone).pack(fill='x',pady=4)
  ttk.Label(frame,textvariable=status,wraplength=590).pack(anchor='w',pady=8)
  ttk.Label(frame,text='每日任务状态请在投资页面查看；本窗口不会额外发送Bark。\n关闭窗口仅停止网页分享，后台日报任务独立运行；重启沿用原链接。',wraplength=590).pack(anchor='w',pady=8)
  def first_analysis():
   # Only self-loopback: no Bark, schedule or cross-service send.
   from urllib.request import Request, build_opener, ProxyHandler
   client = build_opener(ProxyHandler({}))
   base = f'http://127.0.0.1:{port}/investment/api/'
   try:
    with client.open(Request(base+'session',headers={'Authorization':'Bearer '+key}),timeout=5) as response: csrf=json.load(response)['csrf']
    headers={'Authorization':'Bearer '+key,'X-Goose-CSRF':csrf,'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json'}
    with client.open(Request(base+'analyze',data=b'{}',headers=headers,method='POST'),timeout=5): pass
   except Exception: pass  # The visible refresh button remains available.
  window.protocol('WM_DELETE_WINDOW', cleanup)
  threading.Thread(target=first_analysis,daemon=True).start()
  webbrowser.open(desktop()); window.mainloop()
 finally:
  # Covers Tk init/widget/mainloop/browser exceptions as well as normal close.
  # A failed GUI close can retry its exact child while the window is present.
  if not cleanup(final=True):
   raise RuntimeError('OWNED_PHONE_SHARE_STOP_UNCONFIRMED')
