const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const moduleURL='data:text/javascript;base64,'+Buffer.from(fs.readFileSync(path.join(__dirname,'../../monitor/phone/web/transport.js'),'utf8')).toString('base64');

test('viewer handles snapshots, revocation, retries, and late responses without Agent actions', async()=>{
  const {Transport,request}=await import(moduleURL);
  const originals={fetch:global.fetch,document:global.document,EventSource:global.EventSource,setTimeout:global.setTimeout,clearTimeout:global.clearTimeout};
  const timers=new Map(),streams=[],requests=[],snapshots=[],beats=[];
  let timerID=0,expired=0,offline=0;
  try{
    global.document={hidden:false};
    global.setTimeout=(callback)=>{timers.set(++timerID,callback);return timerID;};
    global.clearTimeout=id=>timers.delete(id);
    global.EventSource=class{
      constructor(url){this.url=url;this.listeners={};streams.push(this);}
      addEventListener(name,callback){this.listeners[name]=callback;}
      close(){this.closed=true;}
    };
    global.fetch=async(url,options)=>{
      requests.push([url,options]);
      return {status:200,ok:true,json:async()=>url.endsWith('snapshot')?{revision:1,tasks:[]}:{} };
    };
    const transport=new Transport(v=>snapshots.push(v),v=>beats.push(v),()=>offline++,()=>expired++);
    await transport.start();
    assert.equal(snapshots.length,1);
    assert.equal(streams[0].url,'/api/v1/events');
    assert.equal(typeof streams[0].listeners['device-saved'],'function');
    streams[0].listeners['device-saved']();
    streams[0].listeners.snapshot({data:JSON.stringify({revision:2})});
    streams[0].listeners.heartbeat({data:JSON.stringify({producer_age_ms:20})});
    assert.equal(snapshots.at(-1).revision,2);
    assert.equal(beats[0].producer_age_ms,20);
    streams[0].onerror();
    assert.equal(offline,1);assert.equal(timers.size,1);assert.ok(streams[0].closed);
    await transport.start();
    assert.equal(timers.size,0);assert.equal(transport.failures,0);
    streams.at(-1).listeners['session-ended']();
    assert.equal(expired,1);assert.equal(transport.running,false);
    assert.ok(requests.every(([url,options])=>url.startsWith('/api/v1/')&&!options.method));
    global.fetch=async()=>({status:401,ok:false,json:async()=>({error:'expired'})});
    await assert.rejects(request('/api/v1/session'),e=>e.status===401);
    await transport.start();assert.equal(expired,2);
    // A slow response arriving after stop must not revive the page or open SSE.
    let release;const before=snapshots.length;
    global.fetch=()=>new Promise(resolve=>{release=()=>resolve({status:200,ok:true,json:async()=>({})});});
    const pending=transport.start();transport.stop();release();
    // Allow the second fetch only to finish; it must not publish anything.
    global.fetch=async()=>({status:200,ok:true,json:async()=>({revision:99})});
    await pending;assert.equal(snapshots.length,before);
  }finally{Object.assign(global,originals);}
});


test('viewer clears stale data, freezes elapsed time, and re-fetches unchanged content on recovery',()=>{
  const vm=require('node:vm');
  class Element{
    constructor(){this.children=[];this.style={setProperty(){},removeProperty(){}};this.dataset={};this.attributes={};this.classList={toggle(){},remove(){}};this.hidden=false;this.checked=false;}
    append(...items){this.children.push(...items);}
    replaceChildren(...items){this.children=items;}
    setAttribute(key,value){this.attributes[key]=value;}
    getAttribute(key){return this.attributes[key]||null;}
    removeAttribute(key){delete this.attributes[key];}
    addEventListener(){}
  }
  let imageAssignments=0;
  const elements=new Map(),element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const transports=[];let now=0;
  const context=vm.createContext({
    document:{documentElement:new Element(),getElementById:element,createElement:()=>new Element(),addEventListener(){},hidden:false},
    window:{scrollY:0,scrollTo(){},addEventListener(){},getSelection:()=>null},
    sessionStorage:{getItem:()=>null,setItem(){}},performance:{now:()=>now},setInterval(){},
    request:()=>new Promise(()=>{}),
    Transport:class{constructor(){this.starts=0;transports.push(this);}start(){this.starts++;}stop(){}},
    testSnapshot:{schema_version:1,epoch:'one',revision:1,server_time_ms:120000,producer_age_ms:0,
      desktop_focus_key:'task',privacy_mode:'summary',appearance:{},tasks:[{
        task_key:'task',app_id:'codex',source:'Codex',status:'running',title:'fixture',summary:'safe summary',
        timer:{started_at_ms:1000,final:false},chain:{nodes:[],completed:null},detail_blocks:[],connection_state:'ready'
      }]}
  });
  let source=fs.readFileSync(path.join(__dirname,'../../monitor/phone/web/app.js'),'utf8').replace(/^import .*?;\r?\n/,'');
  context.document.documentElement.style.setProperty=(key)=>{if(key==='--page-wallpaper')imageAssignments++;};
  context.testSnapshot.appearance={images:{page_wallpaper:'a'.repeat(64),wallpaper:'a'.repeat(64)}};
  vm.runInContext(source,context);
  vm.runInContext('accept(testSnapshot)',context);
  assert.equal(element('task-strip').children.length,1);
  assert.equal(element('timer').textContent,'01:59');
  vm.runInContext('render();render();render()',context);
  assert.equal(imageAssignments,1,'unchanged task renders must not reload wallpaper');
  vm.runInContext('heartbeat({server_time_ms:130000,producer_age_ms:10000})',context);
  const frozen=element('timer').textContent;
  vm.runInContext('heartbeat({server_time_ms:140000,producer_age_ms:20000})',context);
  assert.equal(element('timer').textContent,frozen);
  vm.runInContext('heartbeat({server_time_ms:190000,producer_age_ms:70000})',context);
  assert.equal(element('task-strip').children.length,0);
  assert.equal(element('capsule-title').textContent,'等待恢复');
  vm.runInContext('heartbeat({server_time_ms:191000,producer_age_ms:0})',context);
  assert.equal(transports[0].starts,1);
  // Full snapshot has the same revision: it must still refill the cleared UI.
  vm.runInContext('accept({...testSnapshot, tasks:[{task_key:"restored",source:"Codex",status:"running",chain:{nodes:[]}}]})',context);
  assert.equal(element('task-strip').children.length,1);
  assert.equal(element('connection').textContent,'● 已连接');
});
