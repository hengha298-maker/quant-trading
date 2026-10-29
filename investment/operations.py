"""Read actual task state and maintain an explicit local analysis pause switch."""
from pathlib import Path
from datetime import datetime,timedelta
import json,os,re,subprocess,time,uuid
from .holdings import _safe_path,_canonical,_now
_cached=None;_cached_at=0

def _task_status():
 if os.name!='nt':return {'known':False,'enabled':None,'state':'unavailable'}
 script="""$ErrorActionPreference='Stop';$t=Get-ScheduledTask -TaskName 'Goose-Investment-Pre1445' -TaskPath '\\';$i=Get-ScheduledTaskInfo -TaskName $t.TaskName -TaskPath '\\';[pscustomobject]@{known=$true;enabled=$t.Settings.Enabled;state=[string]$t.State;lastRun=$i.LastRunTime.ToString('o');lastResult=$i.LastTaskResult;nextRun=$i.NextRunTime.ToString('o');ordinaryUser=([int]$t.Principal.RunLevel -eq 0);barkExplicit=($t.Actions[0].Arguments.Contains('--confirm-bark-send'));oldV1PathReferenced=($t.Actions[0].Arguments.Contains('quant-trading-main'))}|ConvertTo-Json -Compress"""
 try:
  p=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
  v=json.loads(p.stdout.decode('utf-8-sig'))
  if p.returncode or type(v) is not dict or type(v.get('enabled')) is not bool:raise ValueError()
  return v
 except (OSError,ValueError,subprocess.TimeoutExpired):return {'known':False,'enabled':None,'state':'unavailable'}

def pause_state(root):
 folder=_safe_path(Path(root)/'investment/work/private/automation-controls',missing=True)
 if not folder.exists():return {'paused':False,'source':'default-unpaused'}
 records=[]
 for p in folder.iterdir():
  _safe_path(p,file=True)
  if p.suffix!='.json' or p.stat().st_size>4096:raise ValueError('PAUSE_STORE_INVALID')
  record=json.loads(p.read_text(encoding='utf8'))
  if type(record) is not dict or record.get('format')!='goose-analysis-control' or type(record.get('version')) is not int or record['version']!=1 or type(record.get('paused')) is not bool:raise ValueError('PAUSE_STORE_INVALID')
  records.append((p.name,record))
 return sorted(records)[-1][1] if records else {'paused':False,'source':'default-unpaused'}

def set_paused(root,paused,*,confirmed=False):
 if type(paused) is not bool or confirmed is not True:raise ValueError('EXPLICIT_PAUSE_REQUIRED')
 folder=_safe_path(Path(root)/'investment/work/private/automation-controls',missing=True);folder.mkdir(parents=True,exist_ok=True)
 record={'format':'goose-analysis-control','version':1,'paused':paused,'at':_now().isoformat(),'source':'user-confirmed-private-interface'}
 # Windows clocks may repeat a timestamp. Atomic exclusive names establish a
 # total append order, including concurrent writes and a clock moving backward.
 # Keep old random-suffixed records readable; never rewrite control history.
 for attempt in range(16):
  pause_state(root)
  names=[p.name.split('-')[0] for p in folder.iterdir()]
  stamp=_now().strftime('%Y%m%dT%H%M%S%f')
  if names and max(names)>=stamp:
   try:stamp=(datetime.strptime(max(names),'%Y%m%dT%H%M%S%f')+timedelta(microseconds=1)).strftime('%Y%m%dT%H%M%S%f')
   except ValueError:raise ValueError('PAUSE_STORE_INVALID') from None
  try:
   with (folder/(stamp+'-control.json')).open('xb') as f:f.write(_canonical(record)+b'\n')
   return record
  except FileExistsError:continue
 raise ValueError('PAUSE_CONTROL_WRITE_BUSY')

def automation_status(root,task_reader=None):
 from .daily import latest_daily,schedule_plan
 global _cached,_cached_at
 if task_reader is not None:task=task_reader()
 elif _cached is not None and time.monotonic()-_cached_at<15:task=_cached
 else:task=_task_status();_cached=task;_cached_at=time.monotonic()
 try:control=pause_state(root)
 except (ValueError,OSError):control={'paused':True,'error':'PAUSE_STORE_INVALID'}
 return {'enabled':task.get('enabled'),'task':task,'control':control,'schedule':schedule_plan(),'latest':latest_daily(root),
  'mode':'real-account-readonly-analysis','executionEnabled':False,
  'brokerAccess':{'华宝证券':'本人权限尚未核实，未连接券商账户','银河证券':'本人权限尚未核实，未连接券商账户'},
  'calendar':{'verifiedThrough':'2026-12-31','nextYear':'2027官方完整休市表尚未核验，仍阻断，不按周一至周五猜测'}}
