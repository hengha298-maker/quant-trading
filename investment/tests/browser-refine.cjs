/* Isolated, fictional development UI regression. No user browser/profile/data. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');

const root=path.resolve(__dirname,'../..');
const dependencyRoot=process.env.GOOSE_PLAYWRIGHT_MODULES||path.join(os.homedir(),'.cache','codex-runtimes','codex-primary-runtime','dependencies','node','node_modules');
let chromium;
try{({chromium}=require(path.join(dependencyRoot,'playwright')));}catch(error){console.error('SKIP: existing bundled Playwright is unavailable; no download attempted.');process.exitCode=2;return;}
const python=process.env.GOOSE_RESEARCH_PYTHON||path.resolve(root,'../Runtime/Research32/Scripts/python.exe');
const port=Number(process.env.GOOSE_FICTIONAL_UI_PORT||9495);
assert.ok(Number.isInteger(port)&&port>=1024&&port<=65535);
const key='fictional-browser-test-key-not-a-user-secret-2026';
const base=`http://127.0.0.1:${port}`;
const link=base+'/investment/#key='+key;
const serverSource=String.raw`
import sys,json,tempfile,threading
from pathlib import Path
from datetime import datetime,time,timezone,timedelta
from decimal import Decimal
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
from investment.engine import build_analysis,compatible_report
from investment.server import make_server
from investment import operations
operations._task_status=lambda:{'known':True,'enabled':True,'state':'Ready','lastRun':None,'nextRun':None}
from goose_research.market import xshg_sessions
now=datetime.now(timezone(timedelta(hours=8)));day=now.date().isoformat()
days=xshg_sessions((now-timedelta(days=180)).date().isoformat(),day)[-80:]
symbols=['600900.SH','515450.SH'];histories={};quotes={};adjusted={}
for symbol in symbols:
 bars=[]
 for i,date in enumerate(days):
  p=Decimal('20')+Decimal(i)/100 if symbol=='600900.SH' else Decimal('1.2')+Decimal(i)/1000
  bars.append({'date':date,'open':str(p),'high':str(p),'low':str(p),'close':str(p),'volume':str(100000+i*1000)})
 histories[symbol]={'symbol':symbol,'adjustment':'none','quality':{'status':'validated'},'bars':bars}
 adjusted[symbol]={'symbol':symbol,'adjustment':'qfq','quality':{'status':'validated-basis-only','ohlcAndCalendarValidated':True},'bars':[dict(row) for row in bars]}
 stamp=now.replace(hour=15,minute=0,second=0,microsecond=0) if now.time().replace(tzinfo=None)>=time(15) else now-timedelta(seconds=1)
 status='closing-snapshot' if now.time().replace(tzinfo=None)>=time(15) else 'intraday-verified'
 quotes[symbol]={'symbol':symbol,'price':bars[-1]['close'],'adjustment':'none','source':'FICTIONAL-UI-TEST-NOT-REAL-QUOTES','sourceTimestamp':stamp.isoformat(),'quality':{'status':status,'unitVerified':True,'intradaySignalAllowed':status=='intraday-verified'}}
holdings={'asOf':day,'availableCash':'9876.54','missingBrokers':[], 'accounts':[], 'positions':[
 {'symbol':'600900.SH','name':'FICTIONAL 600900.SH 长江测试600900','quantity':'100','costPrice':'20.00','reportedMarketValue':'2222.22','reportedPnl':'333.33','holdingAsOf':day,'broker':'虚构甲证券'},
 {'symbol':'515450.SH','name':'FICTIONAL 515450.SH ETF测试515450','quantity':'200','costPrice':'1.00','reportedMarketValue':'222.22','reportedPnl':'11.11','holdingAsOf':day,'broker':'虚构乙证券'}]}
report=build_analysis({'quotes':quotes,'histories':histories,'adjustedHistories':adjusted,'diagnostics':[{'fixture':True,'actualDataUsed':False}]},holdings,now=now)
report['id']='fictional-isolated-browser-regression';report['summary']='完全虚构的界面测试，不是真实持仓或行情。'
report['portfolio']['concentration']='89.123456789123456789'
from investment.tests.test_strategy_lab import history,SPEC
from investment.strategy_lab import run_suite,summarized
from investment.holdings import _canonical
import hashlib
lab=run_suite(history('510050.SH'),history(),SPEC,allow_synthetic=True)
key=lab['inputHash'];lab_summary=summarized(lab,key)
candidate={'name':'FICTIONAL ETF 测试','symbol':'510050.SH','kind':'etf','through':day,'quality':{'status':'fictional-ui-only'},'score':{'value':70,'parts':[{'factor':'虚构指标','points':70,'maxPoints':100,'basis':'完全虚构的界面测试'}]},'reasons':['FICTIONAL 仅测试显示'],'trigger':'FICTIONAL 观察条件','invalidation':'FICTIONAL 失效条件','risks':['不是投资研究结果'],'rejections':[],'lab':lab_summary}
from investment.total_return import compare
from investment.trust_research import summarize as trust_summary
from investment.radar_market import complete_session
fictional_doc={'format':'goose-corporate-actions','version':1,'sources':{'fixture':{'url':'https://www.sse.com.cn/FICTIONAL-UNIT-TEST.pdf','sha256':'a'*64,'publicationDate':'2024-01-01','locator':'FICTIONAL UI ONLY','file':'fictional.pdf'}},'events':[],'coverage':[{'symbol':'510050.SH','from':'2024-01-01','to':'2026-12-31','status':'reviewed-complete-interval','types':['cash-dividend','share-ratio'],'sourceIds':['fixture'],'eventIds':[]}]}
trust_study=compare(history('510050.SH')['raw'],history()['raw'],SPEC,fictional_doc,allow_synthetic=True)
trust_key=hashlib.sha256(b'FICTIONAL-TRUST-STUDY').hexdigest();trust_study['inputHash']=trust_key
candidate['through']=complete_session(now)
candidate['trustResearch']={'status':'ready','dataTrustGrade':'TEST','dataTrustLabel':'FICTIONAL 完全虚构的证据展示','advantageConclusion':'尚未证明优于买入持有','sourceEvidence':{'from':'2024-09-02','to':trust_study['to'],'verifiedEvents':0,'complete':False,'missingIntervals':[{'from':'2026-01-01','to':trust_study['to']}],'sources':[],'events':[]},'current':trust_summary(trust_study,trust_key),'verifiedWindow':trust_summary(trust_study,trust_key)}
report['testData']=True
report['opportunityRadar']={'synthetic':False,'through':complete_session(now),'groups':{'etf':{'candidates':[candidate]},'stock':{'candidates':[],'message':'暂无符合条件的机会'}},'items':[candidate],'limitations':['FICTIONAL TEST ONLY']}
for instrument in report['instruments']:
 instrument['name']=next(p['name'] for p in holdings['positions'] if p['symbol']==instrument['symbol'])
work=root/'investment'/'work';work.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='fictional-browser-',dir=work) as temp:
 fake=Path(temp).resolve();fake.relative_to(work.resolve())
 assert not fake.is_symlink() and not fake.is_junction()
 run=fake/'investment'/'work'/'runs'/'fixture';run.mkdir(parents=True)
 (run/'report.json').write_text(json.dumps(report,ensure_ascii=False),encoding='utf8')
 labdir=fake/'investment/work/radar/labs';labdir.mkdir(parents=True)
 (labdir/(key+'.json')).write_bytes(_canonical({'result':lab,'sha256':hashlib.sha256(_canonical(lab)).hexdigest()}))
 (labdir/(trust_key+'.json')).write_bytes(_canonical({'result':trust_study,'sha256':hashlib.sha256(_canonical(trust_study)).hexdigest()}))
 (run/'goose-report-v31.json').write_text(json.dumps(compatible_report(report),ensure_ascii=False),encoding='utf8')
 server=make_server(fake,port=int(sys.argv[2]),access_key=sys.argv[3],analyze=lambda _:report)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 print('FICTIONAL_SERVER_READY',flush=True)
 try:sys.stdin.readline()
 finally:server.shutdown();server.server_close();thread.join(timeout=5)
`;

async function main(){
 let browser,server,stderr='',stdout='',closed=false;
 const errors=[],external=[],failed=[];
 try{
  server=spawn(python,['-I','-B','-c',serverSource,root,String(port),key],{cwd:root,stdio:['pipe','pipe','pipe'],windowsHide:true});
  server.stdout.on('data',data=>{stdout+=data;});server.stderr.on('data',data=>{stderr+=data;});server.once('exit',()=>{closed=true;});
  await new Promise((resolve,reject)=>{const start=Date.now();const timer=setInterval(()=>{if(stdout.includes('FICTIONAL_SERVER_READY')){clearInterval(timer);resolve();}else if(closed||Date.now()-start>20000){clearInterval(timer);reject(new Error('Fictional server did not start: '+stderr.slice(-3000)));}},100);});
  browser=await chromium.launch({channel:'msedge',headless:true,args:['--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--disable-default-apps','--disable-domain-reliability','--metrics-recording-only']});
  for(let round=0;round<2;round++){
   const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1,isMobile:true,hasTouch:true});
   try{
    await context.route('**/*',route=>{if(new URL(route.request().url()).origin===base)return route.continue();external.push(route.request().url());return route.abort();});
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
    page.on('response',response=>{if(response.status()>=400)failed.push({status:response.status(),path:new URL(response.url()).pathname});});
    await page.goto(link,{waitUntil:'networkidle'});
    await page.locator('#report .instrument').first().waitFor();
    assert.match(await page.locator('#connection').innerText(),/已连接私人电脑服务/);
    assert.equal(new URL(page.url()).hash,'#key='+key,'bookmark fragment must survive in both fresh contexts');
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('goose-private-access')),key);
    assert.equal(await page.locator('#report .instrument').count(),2);
    assert.ok((await page.locator('#opportunity-radar').innerText()).includes('每日投资机会雷达'));
    assert.ok((await page.locator('#opportunity-radar').innerText()).includes('暂无符合条件的机会'));
    const candidate=page.locator('#opportunity-radar .scenario').first();await candidate.locator('summary').click();assert.match(await candidate.innerText(),/FICTIONAL 失效条件/);
    assert.match(await candidate.innerText(),/尚未证明优于买入持有/);assert.match(await candidate.innerText(),/当前完整含息回测未通过覆盖检查/);
    const titles=await page.locator('#report .instrument > .report-head h3').allTextContents();
    for(const [i,code] of ['600900','515450'].entries())assert.equal((titles[i].match(new RegExp(code,'g'))||[]).length,1,'stock code duplicated within label');
    const content=await page.locator('#report').innerText();
    assert.match(content,/89\.12%/);assert.ok(!content.includes('89.123456789'));
    assert.match(content,/按成本价重估差额/);assert.ok(content.includes('导入/录入盈亏（原值）'));assert.match(content,/¥ 344\.44/);
    assert.ok(await page.locator('#report .scenario').count()>=8);
    const scenario=page.locator('#report .scenario').first();
    await scenario.locator('summary').click();assert.equal(await scenario.getAttribute('open'),'');
    assert.ok((await scenario.innerText()).includes('情景只用于人工研究'));
    await scenario.locator('summary').click();assert.equal(await scenario.getAttribute('open'),null);
    const layout=await page.evaluate(()=>({viewport:innerWidth,width:document.documentElement.scrollWidth,body:document.body.scrollWidth}));
    assert.ok(layout.width<=layout.viewport+1&&layout.body<=layout.viewport+1,'390px viewport overflow: '+JSON.stringify(layout));
    assert.equal(await page.locator('nav .tab').count(),4);
    await page.locator('[data-page="research"]').click();
    assert.equal(await page.locator('#research-report .card').count(),4);
    assert.ok((await page.locator('#research-report').innerText()).includes('MA60'));
    assert.equal(await page.locator('#research-report svg').count(),3);
    const lab=page.locator('#research-report .card').filter({hasText:'固定规则策略实验室'}).first();await lab.locator('details > summary').first().click();assert.match(await lab.innerText(),/固定规则|留出收益/);
    const labDownload=page.waitForEvent('download');await lab.getByRole('button',{name:'下载完整资金曲线、信号及成交记录'}).click();assert.ok(JSON.parse(fs.readFileSync(await (await labDownload).path(),'utf8')).result.synthetic);
    const trusted=page.locator('#research-report .card').filter({hasText:'可信回测 · 企业行为与现金分红'}).first();await trusted.locator('details > summary').first().click();await trusted.locator('summary').filter({hasText:'当前期限：因果信号与价格回测'}).click();assert.match(await trusted.innerText(),/含息留存收益/);assert.match(await trusted.innerText(),/完全虚构的测试研究/);
    const trustDownload=page.waitForEvent('download');await trusted.getByRole('button',{name:'下载此区间完整含息／价格研究记录'}).click();assert.ok(JSON.parse(fs.readFileSync(await (await trustDownload).path(),'utf8')).result.synthetic);
    await page.locator('[data-page="holdings"]').click();
    await page.locator('#broker').selectOption('华宝证券');
    await page.locator('#trade-start').fill('2026-10-27');await page.locator('#trade-end').fill('2026-10-28');
    const fictionalCSV='成交编号,成交日期,操作,证券代码,成交数量,成交价格,成交金额,总费用,资金发生额\nBROWSER-FICTIONAL-1,2026-10-27,买入,600900,10,10,100,,\n';
    await page.locator('#trade-file').setInputFiles({name:'fictional.csv',mimeType:'text/csv',buffer:Buffer.from(fictionalCSV)});
    await page.locator('#trade-form button[type="submit"]').click();await page.locator('#trade-confirm').waitFor({state:'visible'});
    assert.ok((await page.locator('#trade-preview').innerText()).includes('新增1条'));
    await page.locator('#trade-confirm').click();await page.waitForFunction(()=>document.querySelector('#trade-message').textContent.includes('已追加1条'));
    await page.locator('#ledger-state button').waitFor();page.once('dialog',dialog=>dialog.accept());
    await page.locator('#ledger-state button').click();await page.waitForFunction(()=>document.querySelectorAll('#ledger-state button').length===0);
    const accountLayout=await page.evaluate(()=>({viewport:innerWidth,width:document.documentElement.scrollWidth}));assert.ok(accountLayout.width<=accountLayout.viewport+1,'holdings page overflow');
    assert.ok((await page.locator('#automation-status').innerText()).includes('已启用'));
    if(round===0){
     await page.locator('summary').filter({hasText:'补充期初／期末对账证据'}).click();
     await page.locator('#statement-date').fill('2026-10-26');await page.locator('#statement-cost-basis').selectOption('moving-average-acquisition-including-buy-fees');await page.locator('#statement-complete').check();
     const statementCSV='证券代码,持仓数量,持仓成本金额,证券市值,账面现金\n600900,100,1000,1000,1000\n';
     await page.locator('#statement-file').setInputFiles({name:'fictional-statement.csv',mimeType:'text/csv',buffer:Buffer.from(statementCSV)});
     await page.locator('#statement-form button[type=submit]').click();await page.locator('#statement-confirm').waitFor({state:'visible'});
     assert.ok((await page.locator('#ledger-tools-status').innerText()).includes('1000'));
     await page.locator('#statement-confirm').click();await page.waitForFunction(()=>document.querySelector('#ledger-tools-status').textContent.includes('已追加对账证据'));
    }
    await page.locator('summary').filter({hasText:'完整加密备份与恢复'}).click();
    const fictionalPhone=await page.evaluate(async()=>{
     const io=await import('/v3/web/io.js'),domain=await import('/v3/web/domain.js');await io.loadState();
     const state=domain.saveAccount(domain.emptyState(),{name:'FICTIONAL-BROWSER-BACKUP-BANK',type:'bank',openingBalance:'10000',openingDate:'2026-01-01'});await io.persistState(state);return state;
    });
    await page.locator('#full-password').fill('fictional-browser-complete-password');await page.locator('#full-password-repeat').fill('fictional-browser-complete-password');
    const download=page.waitForEvent('download');await page.locator('#full-export').click();const encrypted=fs.readFileSync(await (await download).path(),'utf8');
    assert.ok(!encrypted.includes('FICTIONAL-BROWSER-BACKUP-BANK'));assert.ok(!encrypted.includes('broker-events'));
    const decrypted=await page.evaluate(async text=>(await import('/investment/backup.js')).decryptFullBackup(text,'fictional-browser-complete-password'),encrypted);
    assert.deepEqual(decrypted.phoneState,fictionalPhone);assert.ok(decrypted.ledger.files.length>=2);
    await page.evaluate(async()=>{const io=await import('/v3/web/io.js');await io.persistState((await import('/v3/web/domain.js')).emptyState());});
    await page.locator('#full-restore-file').setInputFiles({name:'fictional-complete.json',mimeType:'application/json',buffer:Buffer.from(encrypted)});
    await page.locator('#full-restore-password').fill('fictional-browser-complete-password');await page.locator('#full-restore-preview').click();
    await page.waitForFunction(()=>document.querySelector('#full-restore-confirm').disabled===false);
    assert.ok((await page.locator('#full-restore-status').innerText()).includes('冲突0项'));
    page.once('dialog',dialog=>dialog.accept());await page.locator('#full-restore-confirm').click();await page.waitForFunction(()=>document.querySelector('#full-restore-status').textContent.includes('恢复合并完成'));
    const restoredPhone=await page.evaluate(async()=>(await import('/v3/web/io.js')).loadState());assert.deepEqual(restoredPhone,fictionalPhone);
    await page.locator('[data-page="overview"]').click();
    const presentation=await context.request.get(base+'/investment/presentation.js');assert.equal(presentation.status(),200);
    if(round===0){
     const artifacts=path.join(root,'investment','work','fictional-browser-artifacts');fs.mkdirSync(artifacts,{recursive:true});
     await page.screenshot({path:path.join(artifacts,'mobile-390.png'),fullPage:true});
    }
   }finally{await context.close();}
  }
  assert.deepEqual(errors,[],'JavaScript orconsoleerrors');assert.deepEqual(external,[],'unexpectedexternalrequests');assert.deepEqual(failed,[],'HTTPerrors');
  console.log(JSON.stringify({passed:true,fictionalDataOnly:true,headless:true,viewport:390,freshContexts:2,externalRequests:0,javascriptErrors:0,checks:['rendering','asset-200','unique-symbol-label','two-decimal-percent','broker-vs-computed-pnl','scenario-toggle','bookmark-fragment','no-horizontal-overflow','four-pages','ma60-chart','opportunity-radar','candidate-trigger-invalidation','no-forced-candidates','five-strategy-holdout-table','authenticated-full-curve-download','trade-preview-confirm-undo','real-task-state-rendering','complete-encrypted-download','restore-preview-confirm','phone-and-server-ledger-scope']},null,2));
 }finally{
  if(browser)await browser.close();
  if(server&&!closed){server.stdin.write('stop\n');server.stdin.end();await Promise.race([new Promise(resolve=>server.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,5000))]);if(!closed)server.kill();}
 }
}
main().catch(error=>{console.error(error.stack||String(error));process.exitCode=1;});
