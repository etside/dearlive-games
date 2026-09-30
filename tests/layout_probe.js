// Run the real layout and print the numbers the renderer uses.
const fs=require('fs'),path=require('path'),vm=require('vm');
const CLIENT=path.join('/root/dearlive-games','games','teen_patti_pro','client');
const src=fs.readFileSync(path.join(CLIENT,'game.js'),'utf8');
const calls=[];
const ctx=new Proxy({measureText:t=>({width:String(t).length*6}),
  createLinearGradient:()=>({addColorStop(){}}),createRadialGradient:()=>({addColorStop(){}})},
  {get(t,p){if(p in t)return t[p];if(p==='then'||p===Symbol.toPrimitive)return undefined;
    return (...a)=>{calls.push(String(p)+' '+a.map(v=>typeof v==='number'?Math.round(v):(typeof v==='string'?v.slice(0,12):'')).join(','));};},
   set(){return true;}});
const el=(id,tag)=>({id,tagName:(tag||'div').toUpperCase(),dataset:{},style:{},className:'',
  children:[],hidden:false,textContent:'',innerHTML:'',value:'',width:390,height:844,
  clientWidth:390,clientHeight:844,classList:{add(){},remove(){},contains:()=>false},
  appendChild(c){this.children.push(c);return c;},removeChild(c){return c;},remove(){},
  setAttribute(){},getAttribute:()=>null,removeAttribute(){},addEventListener(){},removeEventListener(){},
  getBoundingClientRect:()=>({left:0,top:0,width:390,height:844}),getContext:()=>ctx,focus(){},
  click(){},closest:()=>null,querySelector:()=>null,querySelectorAll:()=>[]});
const nodes={};const document={readyState:'complete',documentElement:{style:{}},head:{appendChild(){}},
  body:{appendChild(){},removeChild(){},style:{}},createElement:t=>el('',t),createTextNode:t=>({textContent:t}),
  getElementById:id=>nodes[id]||(nodes[id]=el(id)),querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}};
const cv=el('tpp-canvas','canvas');cv.getContext=()=>ctx;nodes['tpp-canvas']=cv;
const frames=[];
const sb={console,document,innerWidth:390,innerHeight:844,devicePixelRatio:3,navigator:{userAgent:'node',onLine:true},
  location:{search:'?session=t&room=teen-patti-low',pathname:'/teen-patti-pro/',protocol:'http:',origin:'http://x',href:'http://x/'},
  localStorage:{getItem:()=>null,setItem(){},removeItem(){}},sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},
  performance:{now:()=>Date.now()},requestAnimationFrame:f=>{if(frames.length<2)frames.push(f);return 1;},
  cancelAnimationFrame(){},setTimeout:(f,ms)=>{if(ms<=50)frames.push(f);return 1;},clearTimeout(){},
  setInterval:()=>1,clearInterval(){},matchMedia:()=>({matches:false,addEventListener(){}}),
  addEventListener(){},Image:function(){return{complete:true,naturalWidth:64,naturalHeight:64,set src(v){}};},
  WebSocket:function(){this.close=()=>{}},Blob:function(){},URL:{createObjectURL:()=>'b',revokeObjectURL(){}},
  fetch:()=>Promise.resolve({ok:true,json:()=>Promise.resolve({success:true,data:SNAP})}),
  URLSearchParams:class{constructor(s){this.s=s}get(){return null}},
  Uint8Array,Math,JSON,Date,Object,Array,String,Number,Boolean,isFinite,parseInt,parseFloat,Error,Promise,Set,Map,RegExp,Symbol};
function getComputedStyle(){return{getPropertyValue:()=>''};}
sb.getComputedStyle=getComputedStyle;
sb.window=sb;sb.globalThis=sb;sb.self=sb;
const SNAP=JSON.parse(fs.readFileSync(process.argv[2]||'/tmp/live_snap.json','utf8'));
vm.createContext(sb);
// palaceLayout lives inside the IIFE, so expose it from just inside the close.
const inject = src.replace(/\n\}\)\(\);\s*$/, '\n  globalThis.__L = palaceLayout();\n  globalThis.__PALACE = { palaceLayout, palaceBottom, palaceCards, palacePanels, palaceChairs, palaceToolbar, num, rr, card };\n})();');
vm.runInContext(inject, sb, {filename:'g.js'});
const L=sb.__L;
console.log('canvas 390x844   SAFE.l='+L.seats.A.x.toFixed(0)+' (approx)');
console.log('usable='+L.usable.toFixed(0)+'  colW='+L.colW.toFixed(1)+'  cw='+L.cw.toFixed(1)+'  ch='+L.ch.toFixed(1));
console.log('bands:');Object.keys(L.band).forEach(k=>console.log('   '+k.padEnd(9)+' y='+L.y[k].toFixed(0).padStart(4)+'  h='+(L.usable*L.band[k]).toFixed(0)));
console.log('seat x:',Object.keys(L.seats).map(k=>k+'='+L.seats[k].x.toFixed(0)+',y='+L.seats[k].y.toFixed(0)).join('  '));
console.log('pot:',JSON.stringify(L.pot));
console.log('bottom bar: y='+L.y.bottom.toFixed(0)+' .. '+(L.y.bottom+L.usable*L.band.bottom).toFixed(0)+'   (H='+844+')');
