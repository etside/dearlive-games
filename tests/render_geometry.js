// Report the real on-screen boxes the renderer draws, from the executed client.
const fs=require('fs'),path=require('path'),vm=require('vm');
const CLIENT='/root/dearlive-games/games/teen_patti_pro/client';
const src=fs.readFileSync(path.join(CLIENT,'game.js'),'utf8');
const boxes=[];
const ctx=new Proxy({measureText:t=>({width:String(t).length*6}),
  createLinearGradient:()=>({addColorStop(){}}),createRadialGradient:()=>({addColorStop(){}})},
  {get(t,p){if(p in t)return t[p];
    if(p==='drawImage')return(...a)=>{boxes.push({op:p,x:a[1],y:a[2],w:a[3],h:a[4]});};
    if(p==='fillRect')return(...a)=>{boxes.push({op:p,x:a[0],y:a[1],w:a[2],h:a[3]});};
    if(p==='then'||p===Symbol.toPrimitive)return undefined;
    return ()=>{};},set(){return true;}});
const el=(id,tag)=>({id,tagName:(tag||'div').toUpperCase(),dataset:{},style:{},className:'',
  children:[],hidden:false,textContent:'',innerHTML:'',value:'',width:390,height:844,
  clientWidth:390,clientHeight:844,classList:{add(){},remove(){},contains:()=>false},
  appendChild(c){return c;},removeChild(c){return c;},remove(){},setAttribute(){},getAttribute:()=>null,
  removeAttribute(){},addEventListener(){},removeEventListener(){},
  getBoundingClientRect:()=>({left:0,top:0,width:390,height:844}),getContext:()=>ctx,focus(){},
  click(){},closest:()=>null,querySelector:()=>null,querySelectorAll:()=>[]});
const nodes={};const document={readyState:'complete',documentElement:{style:{}},head:{appendChild(){}},
  body:{appendChild(){},removeChild(){},style:{}},createElement:t=>el('',t),createTextNode:t=>({textContent:t}),
  getElementById:id=>nodes[id]||(nodes[id]=el(id)),querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}};
const cv=el('tpp-canvas','canvas');cv.getContext=()=>ctx;nodes['tpp-canvas']=cv;
const frames=[];
const sb={console,document,innerWidth:390,innerHeight:844,devicePixelRatio:3,
  getComputedStyle:()=>({getPropertyValue:()=>''}),
  navigator:{userAgent:'node',onLine:true},
  location:{search:'?session=t&room=teen-patti-low',pathname:'/teen-patti-pro/',protocol:'http:',origin:'http://x',href:'http://x/'},
  localStorage:{getItem:()=>null,setItem(){},removeItem(){}},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},
  performance:{now:()=>Date.now()},requestAnimationFrame:f=>{if(frames.length<2)frames.push(f);return 1;},
  cancelAnimationFrame(){},setTimeout:(f,ms)=>{if(ms<=50)frames.push(f);return 1;},clearTimeout(){},
  setInterval:()=>1,clearInterval(){},matchMedia:()=>({matches:false,addEventListener(){}}),
  addEventListener(){},Image:function(){return{complete:true,naturalWidth:64,naturalHeight:64,set src(v){}};},
  WebSocket:function(){this.close=()=>{}},Blob:function(){},URL:{createObjectURL:()=>'b',revokeObjectURL(){}},
  fetch:()=>Promise.resolve({ok:true,json:()=>Promise.resolve({success:true,data:JSON.parse(fs.readFileSync('/tmp/live_snap.json','utf8'))})}),
  URLSearchParams:class{constructor(s){this.s=s}get(){return null}},
  Uint8Array,Math,JSON,Date,Object,Array,String,Number,Boolean,isFinite,parseInt,parseFloat,Error,Promise,Set,Map,RegExp,Symbol};
sb.window=sb;sb.globalThis=sb;sb.self=sb;
const inject=src.replace(/\n\}\)\(\);\s*$/,'\n})();');
vm.createContext(sb);vm.runInContext(inject,sb,{filename:'g.js'});
for(let i=0;i<2&&frames.length;i++) frames.shift()(Date.now());
const W=390,H=844;
// A box larger than the canvas that covers it is a cover-fit background, not
// an off-screen element. Only flag boxes that genuinely fall outside.
const off=boxes.filter(b=>!(b.w>=W&&b.h>=H) &&
  (b.x<-2||b.y<-2||b.x+b.w>W+2||b.y+b.h>H+2));
console.log('drawImage/fillRect boxes: '+boxes.length+'   OFF-SCREEN: '+off.length);
if(off.length) off.slice(0,8).forEach(b=>console.log('   OFF x='+Math.round(b.x)+' y='+Math.round(b.y)+' w='+Math.round(b.w)+' h='+Math.round(b.h)));
// cards: 9 expected, grouped in 3 x-bands
const cards=boxes.filter(b=>b.h>40&&b.h<70&&b.w>25&&b.w<60);
const rows={};cards.forEach(c=>{const k=Math.round(c.y/10)*10;rows[k]=(rows[k]||0)+1;});
console.log('card-sized boxes by y-band:',JSON.stringify(rows));
const xs=[...new Set(cards.map(c=>Math.round(c.x/20)*20))].sort((a,b)=>a-b);
console.log('card x starts:',xs.join(','));
console.log('--- all boxes ---');
boxes.forEach((b,i)=>console.log('  '+String(i).padStart(2)+' '+b.op.padEnd(9)+' x='+String(Math.round(b.x)).padStart(5)+' y='+String(Math.round(b.y)).padStart(5)+' w='+String(Math.round(b.w)).padStart(4)+' h='+String(Math.round(b.h)).padStart(4)));
