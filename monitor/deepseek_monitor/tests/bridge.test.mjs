import test from 'node:test';
import assert from 'node:assert/strict';
import {InteractionBroker, validateAnswers} from '../bridge/broker.mjs';
import {startServer} from '../bridge/server.mjs';
import {mkdtemp, mkdir, readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const questions = [
  {id:'language',question:'语言',options:[{label:'Python'},{label:'JS'}]},
  {id:'features',question:'功能',multiSelect:true,options:[{label:'A'},{label:'B'}]},
  {id:'text',question:'说明'},
];
const answer = {answers:[{id:'language',selected:['Python']},{id:'features',selected:['A','B']},{id:'text',selected:[],custom:'中文回答'}]};

function fixture() {
  const events = [
    {seq:0,type:'turn/start',data:{turn:1}},
    {seq:1,type:'tool/call',data:{callId:'call',name:'write_file',arguments:'{"path":"test.txt"}'}},
    {seq:2,type:'approval/asked',data:{id:'audit-id',callId:'call',toolName:'write_file'}},
  ];
  const agent = {id:'session',session:{header:{cwd:'C:/fixture'},snapshotEvents:()=>events},inbox:{nextTurn:[],nextStep:[]}};
  const state = {active:[],settled:[]};
  const client = {};
  const gateway = {
    pendingRemoteEvents:new Map(),remoteEventClients:new Map([['client',client]]),
    wireStream:{open(){}},calls:[],
    async dispatchRpc(endpoint,payload) {
      this.calls.push(payload.args);
      const pending=this.pendingRemoteEvents.get(payload.args.eventId);
      if (pending) {
        this.pendingRemoteEvents.delete(payload.args.eventId);
        if (pending.kind==='approval') events.push({seq:3,type:'approval/decided',data:{id:'audit-id',outcome:payload.args.outcome.value}});
      }
      return {ok:true};
    },
  };
  const ctx={typertGateway:gateway,agents:{get:id=>id===agent.id?agent:undefined,roots:()=>[agent]},
    sessionProjections:{stateOf:()=>({questions:state})},
    userQuestions:{answer(_agent,callId,value){
      if (!state.active.some(q=>q.callId===callId&&q.state==='continued')) return false;
      if (agent.inbox.nextTurn.length) throw Object.assign(new Error('queued'),{code:'REPLY_QUEUED'});
      agent.inbox.nextTurn.push({source:{kind:'user-question-reply',callId},value});return true;
    },async *attachWait(_agent,_call,signal){yield {remainingMs:1000};await new Promise(resolve=>signal.addEventListener('abort',resolve,{once:true}));}},
  };
  const broker=new InteractionBroker(ctx,'C:/fixture');broker.connected=true;broker.clientId='client';broker.generation=1;
  async function request(kind='approval',id='event') {
    const data=kind==='approval'?{toolName:'write_file',callId:'call',reason:'写文件'}:{questions,wait:{callId:'call',timed:true}};
    gateway.pendingRemoteEvents.set(id,{kind,deliveries:new Set([client])});
    await broker.receive({type:'waterfall',eventId:id,agentId:'session',event:kind==='approval'?'approval/request':'user-questions/request',request:data});
    return broker.requests.get(id);
  }
  const command=(item,action,extra={})=>({command_id:'command-1234',instance:broker.instance,generation:broker.generation,
    session_id:item.session_id,request_id:item.id,revision:item.revision,action,...extra});
  return {broker,gateway,ctx,agent,state,events,request,command};
}

test('structured answers validate all IDs, exact options and cardinality',()=>{
  assert.deepEqual(validateAnswers(questions,answer),answer);
  for (const answers of [answer.answers.slice(1),[answer.answers[0],answer.answers[0],answer.answers[2]],
    [{id:'language',selected:['wrong']},...answer.answers.slice(1)],
    [{id:'language',selected:['Python','JS']},...answer.answers.slice(1)],
    [...answer.answers.slice(0,2),{id:'text',selected:[],custom:''}]]) {
    assert.throws(()=>validateAnswers(questions,{answers}),{code:'BAD_ANSWER'});
  }
});

for (const [action,outcome] of [['approve','allowed-once'],['deny','rejected']]) {
  test(`${action} uses live event identity and waits for matching audit`,async()=>{
    const f=fixture();const item=await f.request();
    assert.equal(item.audit_id,'audit-id');
    const result=await f.broker.respond(f.command(item,action));
    assert.equal(result.status,'confirmed');
    assert.equal(f.gateway.calls[0].eventId,'event');
    assert.equal(f.gateway.calls[0].outcome.value,outcome);
    assert.equal(f.broker.snapshot().requests.length,0);
  });
}

test('duplicate command does not execute twice; reused ID with new content fails',async()=>{
  const f=fixture();const item=await f.request();const command=f.command(item,'approve');
  await Promise.all([f.broker.respond(command),f.broker.respond(command)]);
  assert.equal(f.gateway.calls.length,1);
  await assert.rejects(f.broker.respond({...command,action:'deny'}),{code:'DUPLICATE_COMMAND'});
});

test('stale display after another window answers never sends a decision',async()=>{
  const f=fixture();const item=await f.request();f.gateway.pendingRemoteEvents.delete(item.id);
  await assert.rejects(f.broker.respond(f.command(item,'approve')),{code:'STALE_REQUEST'});
  assert.equal(f.gateway.calls.length,0);
});

test('cross-session and old-generation commands are rejected',async()=>{
  const f=fixture();const item=await f.request();
  await assert.rejects(f.broker.respond(f.command(item,'approve',{session_id:'other'})),{code:'STALE_REQUEST'});
  await assert.rejects(f.broker.respond(f.command(item,'approve',{generation:0})),{code:'DISCONNECTED'});
  assert.equal(f.gateway.calls.length,0);
});

test('live child approval is supported without allowing delegated questions',async()=>{
  const f=fixture();f.ctx.agents.roots=()=>[];
  const item=await f.request();
  assert.ok(item);
  assert.equal((await f.broker.respond(f.command(item,'approve'))).status,'confirmed');
  await f.request('question','question-child');
  assert.equal(f.broker.requests.has('question-child'),false);
  assert.equal(f.gateway.calls.at(-1).outcome.kind,'next');
});

test('question uses same event channel and real tool receipt',async()=>{
  const f=fixture();const item=await f.request('question');
  const receipt=await f.broker.respond(f.command(item,'answer',{answer}));
  assert.equal(receipt.status,'submitted');
  assert.deepEqual(f.gateway.calls[0].outcome.value,answer);
  f.events.push({type:'tool/result',data:{message:{toolCallId:'call',isError:false,content:[]}}});
  assert.equal(f.broker.snapshot().receipts[0].status,'confirmed');
});

test('continued reply queues once and confirms only after admission',async()=>{
  const f=fixture();f.state.active.push({callId:'call',state:'continued',questions});
  const item=f.broker.snapshot().requests[0];
  const result=await f.broker.respond(f.command(item,'answer',{answer}));
  assert.equal(result.status,'queued');assert.equal(f.agent.inbox.nextTurn.length,1);
  assert.equal(f.gateway.calls.length,0);assert.equal(f.broker.snapshot().requests.length,0);
  f.state.active=[];f.state.settled=[{callId:'call',answers:answer.answers}];
  assert.equal(f.broker.snapshot().receipts[0].status,'confirmed');
});

test('discarded queued answer becomes answerable again',async()=>{
  const f=fixture();f.state.active.push({callId:'call',state:'continued',questions});
  const item=f.broker.snapshot().requests[0];
  await f.broker.respond(f.command(item,'answer',{answer}));
  f.agent.inbox.nextTurn=[];
  const snapshot=f.broker.snapshot();
  assert.equal(snapshot.requests.length,1);
  assert.equal(snapshot.receipts[0].status,'discarded');
});

test('timed wait is held only while typing lease is alive',async()=>{
  const f=fixture();const item=await f.request('question');
  f.broker.snapshot('typing-client',[item.id]);assert.equal(f.broker.holds.size,1);
  f.broker.clients.get('typing-client').until=0;f.broker.refresh();assert.equal(f.broker.holds.size,0);
});

test('HTTP rejects unpaired callers and origins, accepts paired snapshot',async t=>{
  const root=fileURLToPath(new URL('../../../.artifacts/bridge/',import.meta.url));await mkdir(root,{recursive:true});
  const directory=await mkdtemp(path.join(root,'transport-'));
  const f=fixture();const server=await startServer(f.broker,directory,path.join(directory,'sessions'));
  t.after(()=>server.close());
  const url=`http://127.0.0.1:${server.descriptor.port}/v1/snapshot`;
  const body=JSON.stringify({client_id:'fixture-client',holds:[]});
  const headers={'content-type':'application/json',authorization:`Bearer ${server.descriptor.token}`};
  assert.equal((await fetch(url,{method:'POST',body})).status,403);
  assert.equal((await fetch(url,{method:'POST',body,headers:{...headers,origin:'http://evil.invalid'}})).status,403);
  const response=await fetch(url,{method:'POST',body,headers});
  assert.equal(response.status,200);assert.equal((await response.json()).value.instance,f.broker.instance);
});

