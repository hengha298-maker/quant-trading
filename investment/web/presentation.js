// Display-only decimal rounding; financial calculations stay in Python Decimal.
const unknown=value=>value===null||value===undefined||value==='';
export function decimalDisplay(value, places=2){
 if(unknown(value)||!Number.isInteger(places)||places<0||places>12)return '未知';
 const match=String(value).match(/^(-?)(\d+)(?:\.(\d+))?$/);if(!match)return '未知';
 const sign=match[1],fraction=match[3]||'',scale=10n**BigInt(places);
 let absolute=BigInt(match[2])*scale+BigInt((fraction+'0'.repeat(places)).slice(0,places)||'0');
 if(fraction.length>places&&fraction[places]>='5')absolute+=1n;
 const whole=absolute/scale,part=places?'.'+String(absolute%scale).padStart(places,'0'):'';
 return (sign&&absolute!==0n?'-':'')+whole+part;
}
export const moneyDisplay=value=>unknown(value)?'未知':'¥ '+decimalDisplay(value,2);
export const percentDisplay=value=>unknown(value)?'未知':decimalDisplay(value,2)+'%';
export function bpsDisplay(value){if(unknown(value)||!/^[-]?\d+$/.test(String(value)))return '未知';const n=BigInt(value),a=n<0n?-n:n;return (n<0n?'-':'')+(a/100n)+'.'+String(a%100n).padStart(2,'0')+'%';}
export function instrumentLabel(name,symbol){
 const code=String(symbol||'').match(/\d{6}/)?.[0];if(!code)return name||symbol||'未命名标的';
 let title=String(name||'').replaceAll(String(symbol||''),'').replaceAll(code,'').replace(/[()（）·|]/g,' ').trim().replace(/\s+/g,' ');
 return title?title+' · '+symbol:String(symbol);
}
