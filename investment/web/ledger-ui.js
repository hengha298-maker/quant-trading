import {encryptFullBackup,decryptFullBackup} from './backup.js';
import {canonical,loadState,persistState,previewRestore,restoreMerge,decryptBackup} from '../../v3/web/io.js';
const $=id=>document.getElementById(id),money=x=>x==null?'未知':`¥${x}`;
function line(box,s){const p=document.createElement('p');p.textContent=s;box.append(p);}
export function initLedgerTools(request,reloadLedger){
 let statement=null,recovery=null;
 const message=$('ledger-tools-status');
 $('statement-form').addEventListener('input',()=>{statement=null;$('statement-confirm').hidden=true;});
 $('statement-form').addEventListener('submit',async event=>{event.preventDefault();statement=null;try{
  const file=$('statement-file').files[0];if(!file||file.size>5*1024*1024)throw new Error('请选择不超过5MiB的快照文件');
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
  const result=await request('statement-preview',{broker:$('broker').value,asOf:$('statement-date').value,costBasis:$('statement-cost-basis').value,completeAccount:$('statement-complete').checked,fileName:file.name,content:btoa(binary)});
  statement=result.previewId;const p=result.preview;message.replaceChildren();line(message,`${p.broker} ${p.asOf} 日末快照：${p.positions.length}只证券；账面现金 ${money(p.cashBalance)}；可用 ${money(p.availableCash)}；冻结 ${money(p.frozenCash)}。`);
  for(const row of p.positions)line(message,`${row.symbol}：数量 ${row.quantity}，成本金额 ${money(row.carryingCost)}，市值 ${money(row.marketValue)}，可卖 ${row.availableQuantity??'未知'}。`);
  for(const warning of p.warnings)line(message,warning);$('statement-confirm').hidden=false;
 }catch(e){line(message,e.message);}});
 $('statement-confirm').addEventListener('click',async()=>{if(!statement)return;try{await request('confirm',{previewId:statement,confirmed:true});statement=null;$('statement-confirm').hidden=true;message.replaceChildren();line(message,'已追加对账证据，原持仓没有覆盖。');await reloadLedger();}catch(e){line(message,e.message);}});
 $('full-export').addEventListener('click',async()=>{const button=$('full-export');button.disabled=true;try{
  const password=$('full-password').value;if(password!==$('full-password-repeat').value)throw new Error('两次密码不一致');
  let phone=await loadState();const old=$('legacy-backup-file').files[0];
  if(old){if(old.size>36*1024*1024)throw new Error('旧备份过大');const previous=await decryptBackup(await old.text(),$('legacy-backup-password').value);phone=restoreMerge(phone,previous);}
  const ledger=await request('ledger-backup'),envelope=await encryptFullBackup(phone,ledger,password);
  const blob=new Blob([JSON.stringify(envelope)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='goose-complete-encrypted-'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
  $('full-backup-status').textContent=`已生成加密包：Windows账本${ledger.files.length}个文件；本浏览器账户${phone.accounts.length}个${old?'，已附加旧手机备份':''}。请将文件另存可靠介质；密码不发送电脑、不保存。`;
 }catch(e){$('full-backup-status').textContent=e.message;}finally{button.disabled=false;$('full-password').value='';$('full-password-repeat').value='';$('legacy-backup-password').value='';}});
 const clear=()=>{recovery=null;$('full-restore-confirm').disabled=true;};$('full-restore-file').addEventListener('change',clear);$('full-restore-password').addEventListener('input',clear);
 $('full-restore-preview').addEventListener('click',async()=>{clear();try{
  const file=$('full-restore-file').files[0];if(!file||file.size>36*1024*1024)throw new Error('请选择大小合规的加密包');
  const incoming=await decryptFullBackup(await file.text(),$('full-restore-password').value),current=await loadState(),phonePlan=previewRestore(current,incoming.phoneState);
  const serverPlan=incoming.ledger?await request('ledger-restore-preview',{ledger:incoming.ledger}):null;
  const conflicts=(phonePlan.conflicts?.length||0)+(serverPlan?.conflicts?.length||0);
  recovery={incoming,current:canonical(current),serverPlan};
  $('full-restore-status').textContent=`备份时间 ${incoming.createdAt}；${incoming.ledger?'含Windows新账本':'旧版仅手机数据，不会删除Windows账本'}；Windows新增${serverPlan?.newFileCount||0}个，相同${serverPlan?.identicalCount||0}个；冲突${conflicts}项。只合并，不覆盖。`;
  $('full-restore-confirm').disabled=conflicts>0;
 }catch(e){$('full-restore-status').textContent=e.message;}finally{$('full-restore-password').value='';}});
 $('full-restore-confirm').addEventListener('click',async()=>{if(!recovery||!window.confirm('确认合并此备份的无冲突记录？不会覆盖现有文件，不恢复密钥或定时任务。'))return;
  const button=$('full-restore-confirm');button.disabled=true;let serverCommitted=false;
  try{const current=await loadState();if(canonical(current)!==recovery.current)throw new Error('手机账本已变化，请重新预览');const merged=restoreMerge(current,recovery.incoming.phoneState);
   if(recovery.serverPlan){await request('ledger-restore-confirm',{previewId:recovery.serverPlan.previewId,confirmed:true});serverCommitted=true;}
   if(canonical(merged)!==canonical(current))await persistState(merged);
   recovery=null;$('full-restore-status').textContent='恢复合并完成，原记录保留；已重新读取账本。';await reloadLedger();
  }catch(e){$('full-restore-status').textContent=(serverCommitted?'Windows已安全追加；手机合并未完成，可重新预览重试，不会重复导入。':'未覆盖现有账本。')+e.message;}
 });
}

export function renderAccounting(target,value){
 const section=document.createElement('article');section.className='card';const h=document.createElement('h3');h.textContent='账本对账与含息收益（按证据门控）';section.append(h);
 for(const a of value.accounts){line(section,`${a.broker}：${a.status==='reconciled-declared-coverage'?'所填期间对账通过（用户声明完整）':'待核对'}${a.from?' · '+a.from+'—'+a.to:''}`);
  if(a.cash)line(section,`现金：期初 ${money(a.cash.opening)} ＋到账净额 ${money(a.cash.postedNetChange)} ＝推演 ${money(a.cash.projectedClosing)}；券商期末 ${money(a.cash.brokerClosing)}；差额 ${money(a.cash.difference)}。`);
  for(const p of a.positions||[])line(section,`${p.symbol}：推演/券商数量 ${p.projectedQuantity}/${p.brokerQuantity}，差额 ${p.quantityDifference}；购入成本 ${money(p.acquisitionCost)}；券商成本 ${money(p.brokerCarryingCost)}；重算已实现 ${money(p.realizedPnl)}（券商原报 ${money(p.brokerReportedRealizedPnl)}）；重估未实现 ${money(p.unrealizedPnl)}（券商原报 ${money(p.brokerReportedUnrealizedPnl)}）。`);
  for(const d of a.dividends||[])line(section,`${d.symbol||'分红'}：应收 ${money(d.receivable)}，到账税前 ${money(d.creditedGross)}，净到账 ${money(d.creditedNet)}，独立关联税 ${money(d.separateRelatedTax)}，应收/到账差额 ${money(d.grossDifference)}；未来补扣税未知。`);
  if(a.returns){const r=a.returns;line(section,`已到账红利净额（另列独立税前） ${money(r.creditedDividendNetBeforeSeparateTax)}；独立税 ${money(r.separateTax)}；资金净转入 ${money(r.externalNetInflow)}；区间总收益 ${money(r.accountTotalPnl)}。`);line(section,`无外部流区间收益率 ${r.holdingPeriodReturnPct==null?'未知':r.holdingPeriodReturnPct+'%'}；Modified Dietz估计 ${r.modifiedDietzEstimatedPct==null?'未知':r.modifiedDietzEstimatedPct+'%'}。不冒充资金加权/时间加权收益率。`);}
  for(const i of a.issues||[])line(section,'待核对：'+i.message+(i.period?' '+i.period.from+'—'+i.period.to:''));
 }
 line(section,'合并总收益：'+money(value.combined.accountTotalPnl)+'；'+value.combined.warning);target.append(section);
}
