"""A single local control window. Closing it revokes the temporary read link."""
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
  status=json.loads(result.stdout);name=status.get('Self',{}).get('DNSName','').rstrip('.')
  if status.get('BackendState')!='Running' or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?',name) or not name.endswith('.ts.net'):return None
  return name+':10001'
 except (OSError,ValueError,subprocess.TimeoutExpired):return None

def open_control(root,port=9394):
 from .server import make_server
 from .engine import load_latest
 root=Path(root);phone_host=private_host()
 try:server=make_server(root,port=port,allowed_hosts=[phone_host] if phone_host else [],allowed_origins=['https://'+phone_host] if phone_host else [])
 except OSError:
  t=tk.Tk();t.withdraw();messagebox.showerror('养鹅研究','端口已被占用。保留现有程序，未关闭任何进程。');t.destroy();return
 threading.Thread(target=server.serve_forever,daemon=True).start()
 window=tk.Tk();window.title('养鹅计划 · 投资研究控制台');window.geometry('650x400')
 frame=ttk.Frame(window,padding=24);frame.pack(fill='both',expand=True)
 ttk.Label(frame,text='养鹅投资研究',font=('Microsoft YaHei UI',21,'bold')).pack(anchor='w')
 ttk.Label(frame,text='真实持仓导入 · 当日行情核验 · 中文报告\n原V1/V2/P1和手机离线数据均保留；本程序不下单、不推送。',wraplength=590).pack(anchor='w',pady=12)
 key=server.access_key
 desktop=f'http://127.0.0.1:{port}/investment/#key={key}'
 phone=f'https://{phone_host}/investment/#key={key}' if phone_host else None
 ttk.Button(frame,text='打开投资分析 / 导入最新持仓',command=lambda:webbrowser.open(desktop)).pack(fill='x',ipady=7)
 status=tk.StringVar(value='已启动。打开页面后可更新行情，或导入券商导出文件。')
 def copy_phone():
  if phone is None:status.set('未读取到已连接的Tailscale私网名称，请先连接Tailscale后重新打开控制台。');return
  window.clipboard_clear();window.clipboard_append(phone);status.set('私有手机链接已复制：仅发给你自己，在Safari打开。链接会在关闭本窗口后失效。')
 ttk.Button(frame,text='复制 iPhone 私有报告地址',command=copy_phone).pack(fill='x',ipady=7,pady=12)
 ttk.Label(frame,textvariable=status,wraplength=590).pack(anchor='w',pady=8)
 ttk.Label(frame,text='自动14:45任务尚未启用。Bark尚未发送。\n关闭本窗口会停止本次共享；不会删除任何报告。',wraplength=590).pack(anchor='w',pady=8)
 def close():server.shutdown();server.server_close();window.destroy()
 def first_analysis():
  # Only self-loopback: no Bark, no background schedule or cross-service send.
  import json
  from urllib.request import Request,urlopen,build_opener,ProxyHandler
  client=build_opener(ProxyHandler({}))  # Local IPC only; market retains OS proxy.
  base=f'http://127.0.0.1:{port}/investment/api/'
  try:
   with client.open(Request(base+'session',headers={'Authorization':'Bearer '+key}),timeout=5) as r:csrf=json.load(r)['csrf']
   headers={'Authorization':'Bearer '+key,'X-Goose-CSRF':csrf,'Origin':f'http://127.0.0.1:{port}','Content-Type':'application/json'}
   with client.open(Request(base+'analyze',data=b'{}',headers=headers,method='POST'),timeout=5):pass
  except Exception:pass  # The visible refresh button remains available.
 window.protocol('WM_DELETE_WINDOW',close)
 threading.Thread(target=first_analysis,daemon=True).start()
 webbrowser.open(desktop);window.mainloop()
