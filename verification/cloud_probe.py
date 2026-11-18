"""Bounded public-data Linux validation. No secrets, production writes or notifications."""
import sys,json,time,hashlib,tempfile,socket,ssl,subprocess,shutil,re,os
from pathlib import Path
from datetime import datetime,timedelta,timezone
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.error import HTTPError
from urllib.parse import urlsplit,urljoin
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT,ROOT/'v32',ROOT/'v33'):sys.path.insert(0,str(p))
from investment import market,engine,radar_market,holdings
SYMBOLS=['600900.SH','515450.SH']
class Redirects(HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  if urlsplit(newurl).scheme!='https' or urlsplit(newurl).hostname!=urlsplit(req.full_url).hostname:raise ValueError('CROSS_ORIGIN_REDIRECT_REFUSED')
  return super().redirect_request(req,fp,code,msg,headers,newurl)
def fetch(url):
 try:
  with build_opener(Redirects()).open(Request(url,headers={'User-Agent':'GooseCompatibilityValidation/1.0'}),timeout=7) as r:
   b=r.read(2*1024*1024+1)
   if len(b)>2*1024*1024:raise ValueError('BODY_LIMIT')
   return {'status':r.status,'finalPath':urlsplit(r.geturl()).path,'type':r.headers.get('Content-Type',''),'bytes':len(b)},b
 except HTTPError as e:return {'status':e.code,'finalPath':urlsplit(e.url).path,'bytes':0},b''
 except Exception as e:return {'status':None,'failureType':type(e).__name__},b''
class Assets(HTMLParser):
 def __init__(self):super().__init__();self.urls=[];self.password=False
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='input' and a.get('type')=='password':self.password=True
  if tag=='script' and a.get('src'):self.urls.append(a['src'])
  if tag=='link' and a.get('rel')=='stylesheet' and a.get('href'):self.urls.append(a['href'])
def entry(host):
 out={'host':host,'authenticatedAppTested':False,'mainlandIPhoneTested':False};socket.setdefaulttimeout(5)
 try:out['dns']={'passed':True,'addressCount':len(socket.getaddrinfo(host,443,type=socket.SOCK_STREAM))}
 except Exception as e:out['dns']={'passed':False,'failureType':type(e).__name__}
 try:
  with socket.create_connection((host,443),timeout=5) as s:
   with ssl.create_default_context().wrap_socket(s,server_hostname=host) as t:out['tls']={'passed':True,'version':t.version(),'hostnameVerified':True}
 except Exception as e:out['tls']={'passed':False,'failureType':type(e).__name__}
 origin='https://'+host; info,body=fetch(origin+'/investment/');out['html']=info
 p=Assets()
 try:p.feed(body.decode('utf8'));out['html']['passwordForm']=p.password
 except Exception:pass
 out['resources']=[]
 for ref in p.urls[:4]:
  url=urljoin(origin,ref)
  if urlsplit(url).hostname==host:
   info,_=fetch(url);out['resources'].append({'path':urlsplit(url).path,**info})
 out['unauthenticatedApi']=fetch(origin+'/investment/api/snapshot')[0]
 out['loginGet']=fetch(origin+'/login')[0]
 chrome=shutil.which('google-chrome') or shutil.which('chromium')
 if chrome:
  try:
   with tempfile.TemporaryDirectory(prefix='goose-entry-browser-') as profile:
    r=subprocess.run([chrome,'--headless','--no-sandbox','--disable-gpu','--disable-dev-shm-usage','--user-data-dir='+profile,'--timeout=10000','--virtual-time-budget=3000','--no-first-run','--dump-dom',origin+'/investment/'],capture_output=True,timeout=18)
    html=r.stdout.decode('utf8',errors='replace');out['browser']={'exitCode':r.returncode,'htmlRendered':'<html' in html,'passwordInputRendered':bool(re.search(r'<input[^>]+type="password"',html)),'domBytes':len(r.stdout),'isAuthenticatedApp':False}
  except Exception as e:out['browser']={'failureType':type(e).__name__}
 else:out['browser']={'status':'browser-not-installed'}
 return out

def research(data,root,now):
 if not any(data.get('histories',{}).values()):return {'status':'blocked','reason':'NO_VALIDATED_REAL_HISTORY','reportGenerated':False}
 cat=radar_market.catalog();histories={s:{'raw':v,'adjusted':data['adjustedHistories'][s]} for s,v in data['histories'].items() if v and data['adjustedHistories'].get(s)}
 quality={x['symbol']:{'status':'partial-sample-only','codes':['BOUNDED_TWO_SECURITY_TEST'],'quote':data['quotes'].get(x['symbol'])} for x in cat['instruments']}
 radar={'catalog':cat,'through':radar_market.complete_session(now),'synthetic':False,'histories':histories,'quality':quality}
 for s,b,q in [(SYMBOLS[0],'华宝证券',100),(SYMBOLS[1],'银河证券',200)]:
  preview=holdings.parse_manual([{'symbol':s,'quantity':q,'costPrice':'1.2345'}],now.astimezone(market.BJ).date().isoformat(),b,available_cash='1000')
  holdings.commit_preview(preview,root/'investment/work/private/holdings',confirmed=True)
 def no_network(*a,**k):raise AssertionError('NO_ADDITIONAL_ENGINE_NETWORK_OR_BARK_ALLOWED')
 started=time.monotonic()
 original_build,original_write=engine.build_analysis,engine.write_json
 def labelled_build(*a,**k):
  value=original_build(*a,**k);value['testData']=True;value['validationScope']={'accountType':'fictional','marketData':'real-public-provider','scope':'two-security-compatibility-only'};return value
 def labelled_write(path,value):
  if Path(path).name=='report.json':value['accountMode']='fictional-engineering-account'
  return original_write(path,value)
 with patch('investment.engine.build_analysis',side_effect=labelled_build),patch('investment.engine.write_json',side_effect=labelled_write),patch('investment.market.collect_market',return_value=data),patch('investment.market.collect_quotes',return_value={'quotes':data['quotes'],'diagnostics':data['diagnostics']}),patch('investment.radar.collect',return_value=radar),patch('urllib.request.OpenerDirector.open',side_effect=no_network),patch('socket.socket.connect',side_effect=no_network),patch('investment.delivery.send_daily',side_effect=no_network):
  result=engine.run_analysis(root,deadline=datetime.now(timezone.utc)+timedelta(seconds=100))
 paths=list((root/'investment/work/runs').glob('*/report.json'));assert len(paths)==1
 raw=paths[0].read_bytes();saved=json.loads(raw);assert json.dumps(saved,sort_keys=True)==json.dumps(result,sort_keys=True) and saved['realOrdersEnabled'] is False and saved['notificationSent'] is False
 digest=hashlib.sha256(raw).hexdigest();assert hashlib.sha256(paths[0].read_bytes()).hexdigest()==digest
 return {'status':'passed','inputMarket':'real-public-provider-data','account':'fictional-engineering-fixture-only','reportGenerated':True,'reportBytes':len(raw),'sha256':digest,'readbackIdentical':True,'seconds':round(time.monotonic()-started,3),'reportStatus':result['status'],'marketHealth':result.get('marketHealth'),'technicalMetricsPresent':{i['symbol']:bool(i.get('metrics')) for i in result.get('instruments',[])},'fullMarketScanVerified':False,'radarScope':'two securities only; missing benchmark/peers remain blocked','candidateCount':0,'realAccountRead':False,'barkRequests':0,'realTrades':0}

def main():
 now=datetime.now(timezone.utc);started=time.monotonic();summary={'format':'goose-night-validation','checkedAt':now.isoformat(),'platform':sys.platform,'sample':SYMBOLS,'privateInputs':False,'cloudflareWrites':0,'barkRequests':0,'fullMarketScanVerified':False,'entries':[]};attempts=[]
 original=market._attempt
 def bounded(*args,**kwargs):
  value,raw=original(*args,**kwargs);attempt=1
  if value['errors'] and any(x in {'NETWORK_TIMEOUT','NETWORK_CONNECTION_FAILED','NETWORK_ERROR'} for x in value['errors']):
   attempts.append({k:v for k,v in value.items() if k not in {'value','headers','transport'}}|{'attempt':1});value,raw=original(*args,**kwargs);attempt=2
  attempts.append({k:v for k,v in value.items() if k not in {'value','headers','transport'}}|{'attempt':attempt});return value,raw
 with tempfile.TemporaryDirectory(prefix='goose-public-real-market-') as directory:
  root=Path(directory)
  try:
   with patch('investment.market._attempt',side_effect=bounded):data=market.collect_market(SYMBOLS,root/'investment/work/collection',history_days=260,deadline=now+timedelta(seconds=150),data_root=root)
   summary['providers']=attempts;summary['market']={'historySymbols':[s for s,h in data['histories'].items() if h],'adjustedHistorySymbols':[s for s,h in data['adjustedHistories'].items() if h],'quotes':{s:{'source':q['source'],'sourceTimestamp':q['sourceTimestamp'],'fetchedAt':q['fetchedAt'],'quality':q['quality'],'adjustment':q['adjustment']} for s,q in data['quotes'].items() if q},'barCounts':{s:len(h['bars']) for s,h in data['histories'].items() if h}}
   try:summary['research']=research(data,root,now)
   except Exception as e:summary['research']={'status':'failed','failureType':type(e).__name__,'code':str(e) if re.fullmatch('[A-Z0-9_]{1,100}',str(e)) else 'ENGINE_VALIDATION_FAILED'}
  except Exception as e:summary.update(providers=attempts,market={'status':'failed','failureType':type(e).__name__,'code':str(e) if re.fullmatch('[A-Z0-9_]{1,100}',str(e)) else 'COLLECTION_FAILED'})
 with ThreadPoolExecutor(max_workers=2) as pool:summary['entries']=list(pool.map(entry,['goose-private-report.nvestment-dev.workers.dev','goose-investment-nvestment.pages.dev']))
 import resource
 summary['peakRssKiB']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
 summary['elapsedSeconds']=round(time.monotonic()-started,3)
 summary['status']='passed-historical-sample-validation' if summary.get('research',{}).get('status')=='passed' and len(summary.get('market',{}).get('historySymbols',[]))==2 else 'completed-with-market-blockers'
 print('GOOSE_VALIDATION_RESULT='+json.dumps(summary,ensure_ascii=True,separators=(',',':')))
 # Collected failures are explicit diagnostic results, not claimed as passes.
 return 0
if __name__=='__main__':raise SystemExit(main())
