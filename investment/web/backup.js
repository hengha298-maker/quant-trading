// Passwords never leave this browser. Ledger semantics are also checked by the
// authenticated local server before any append-only recovery is published.
import {canonical,parseJsonStrict,migrateState,decryptBackup} from '../../v3/web/io.js';
const enc=new TextEncoder(),dec=new TextDecoder('utf-8',{fatal:true});
const LIMIT=24*1024*1024,FORMAT='goose-investment-encrypted';
const fail=message=>{throw new Error(message);};
const b64=bytes=>{let s='';for(let i=0;i<bytes.length;i+=32768)s+=String.fromCharCode(...bytes.subarray(i,i+32768));return btoa(s);};
const unb64=(s,max)=>{if(typeof s!=='string'||s.length>Math.ceil(max/3)*4+4||!/^[A-Za-z0-9+/]*={0,2}$/.test(s))fail('备份编码无效');let raw;try{raw=atob(s);}catch{fail('备份编码无效');}if(raw.length>max||btoa(raw)!==s)fail('备份编码无效');return Uint8Array.from(raw,c=>c.charCodeAt(0));};
async function key(password,salt,iterations){if(typeof password!=='string'||password.length<12||password.length>1024)fail('请使用至少12字符的独立备份密码，无法找回');const k=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveKey']);return crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations,hash:'SHA-256'},k,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);}
export async function sha(value){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(canonical(value))))].map(b=>b.toString(16).padStart(2,'0')).join('');}
function payload(input){
 if(!input||input.format!=='goose-investment-full'||input.version!==1||typeof input.createdAt!=='string'||!Number.isFinite(Date.parse(input.createdAt)))fail('完整备份数据版本无效');
 const phoneState=migrateState(input.phoneState),ledger=input.ledger;
 if(ledger!==null&&(!ledger||ledger.format!=='goose-investment-ledger-state'||ledger.version!==1||!Array.isArray(ledger.files)||ledger.files.length>5000||!/^[a-f0-9]{64}$/.test(ledger.manifestSha256)))fail('账本范围或清单无效');
 return {...input,phoneState,ledger};
}
export async function encryptFullBackup(phoneState,ledger,password){
 const doc=payload({format:'goose-investment-full',version:1,createdAt:new Date().toISOString(),phoneState,ledger,
  scope:{windowsLedger:ledger!==null,phoneData:'current-origin-and-explicitly-attached-legacy-backup',secretsIncluded:false}});
 const body={...doc,payloadSha256:await sha(doc)};const bytes=enc.encode(canonical(body));if(bytes.length>LIMIT)fail('完整备份超过24MiB，未截断');
 const header={format:FORMAT,version:1,cipher:'AES-GCM-256',kdf:'PBKDF2-SHA256',iterations:600000,salt:b64(crypto.getRandomValues(new Uint8Array(16))),iv:b64(crypto.getRandomValues(new Uint8Array(12))),createdAt:doc.createdAt};
 const k=await key(password,unb64(header.salt,16),header.iterations);const ciphertext=await crypto.subtle.encrypt({name:'AES-GCM',iv:unb64(header.iv,12),additionalData:enc.encode(canonical(header)),tagLength:128},k,bytes);
 return {...header,ciphertext:b64(new Uint8Array(ciphertext))};
}
export async function decryptFullBackup(input,password){
 const doc=typeof input==='string'?parseJsonStrict(input,LIMIT*1.5):parseJsonStrict(JSON.stringify(input),LIMIT*1.5);
 if(doc?.format==='goose-v3-encrypted')return {format:'goose-investment-full',version:1,createdAt:doc.createdAt,phoneState:await decryptBackup(doc,password),ledger:null,scope:{windowsLedger:false,phoneData:'legacy-only',secretsIncluded:false}};
 const fields=['format','version','cipher','kdf','iterations','salt','iv','createdAt','ciphertext'];
 if(!doc||Object.keys(doc).length!==fields.length||fields.some(k=>!Object.hasOwn(doc,k))||doc.format!==FORMAT||doc.version!==1||doc.cipher!=='AES-GCM-256'||doc.kdf!=='PBKDF2-SHA256'||doc.iterations!==600000||typeof doc.createdAt!=='string'||!Number.isFinite(Date.parse(doc.createdAt)))fail('不支持或损坏的加密备份');
 const salt=unb64(doc.salt,16),iv=unb64(doc.iv,12),bytes=unb64(doc.ciphertext,LIMIT+16);if(salt.length!==16||iv.length!==12||bytes.length<16)fail('加密备份不完整');
 const {ciphertext,...header}=doc;let plain;try{plain=await crypto.subtle.decrypt({name:'AES-GCM',iv,additionalData:enc.encode(canonical(header)),tagLength:128},await key(password,salt,doc.iterations),bytes);}catch{fail('密码错误或文件被改动；现有账本未修改');}
 const decoded=parseJsonStrict(dec.decode(plain),LIMIT),{payloadSha256,...body}=decoded;
 if(payloadSha256!==await sha(body)||body.createdAt!==doc.createdAt)fail('备份内部完整性校验失败');return payload(body);
}
