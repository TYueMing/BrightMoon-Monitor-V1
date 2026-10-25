import {request} from './transport.js';

const $=id=>document.getElementById(id);
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;};
const names={claude:'Claude Code',codex:'Codex',deepseek_harness:'Harness',opencode:'OpenCode',solo_task:'提醒'};
const actionNames={approve_tool:'同意一次',deny_tool:'拒绝',answer_question:'回答',reject_question:'拒绝回答',stop:'停止执行',cancel:'停止执行',cancel_run:'停止执行'};
let profile=null,active=false,fetching=false,lastFetch=0,busy=false,offset=0,taskRevision=-1,actionKey='',generation=0,formSubmit=null,review=null;
let taskPage=false,lastContact=0,taskDialog=false;
let pendingCommand='';try{pendingCommand=sessionStorage.getItem('solo-pending-command')||'';}catch{}
function pending(id){pendingCommand=id;try{if(id)sessionStorage.setItem('solo-pending-command',id);else sessionStorage.removeItem('solo-pending-command');}catch{}$('check-command').hidden=!id;$('resolve-command').hidden=!id;}
const feedback=text=>{$('remote-feedback').textContent=text;};
function button(text,fn){const b=el('button',text);b.type='button';b.onclick=fn;return b;}
function close(){if(busy)return;$('remote-dialog').close();review=null;formSubmit=null;}
function open(title){taskDialog=false;$('remote-fields').replaceChildren();$('remote-form-error').textContent='';$('remote-dialog-title').textContent=title;$('remote-submit').hidden=false;$('remote-submit').disabled=false;$('remote-submit').textContent='保存';$('remote-dialog').showModal();}
function field(label,type,value){const wrap=el('label',label);const input=el(type==='textarea'?'textarea':'input');if(type!=='textarea')input.type=type;input.value=value??'';wrap.append(input);$('remote-fields').append(wrap);return input;}
function check(label,value){const wrap=el('label',undefined,'check-row'), input=el('input');input.type='checkbox';input.checked=!!value;wrap.append(input,el('span',label));return {wrap,input};}
function can(key){return active&&!busy&&profile?.grants?.[key];}
function reconcileReview(){
  if(!review)return;
  const current=profile?.actions?.find(a=>a.id===review.id);
  if(!active||!current||current.revision!==review.revision){$('remote-submit').disabled=true;$('remote-form-error').textContent='请求已变化或连接中断。请关闭后重新打开当前请求。';}
}
async function refresh(){
  if(!active||fetching||document.hidden)return;
  const gen=generation;fetching=true;lastFetch=Date.now();
  try{
    const p=await request('/api/v2/control');if(gen!==generation)return;
    profile=p;
    if(p.protocol_version!==2)throw new Error('服务协议不兼容，请更新电脑程序');
    renderActions();reconcileReview();
    $('new-task').hidden=!p.grants.edit_tasks;
    $('task-permission').textContent=p.grants.view_tasks?'完整任务目录 · 时间按任务保存的时区解释':'请在电脑长期保存此设备，并开启“查看完整待办”权限。';
    if(!p.grants.view_tasks){$('task-list').replaceChildren();$('more-tasks').hidden=true;if(taskDialog){$('remote-dialog').close();$('remote-fields').replaceChildren();formSubmit=null;taskDialog=false;}}
    if(taskPage&&p.grants.view_tasks&&taskRevision!==p.task_revision)await loadTasks();
  }catch(e){if(gen!==generation)return;profile=null;$('remote-actions').replaceChildren();$('remote-actions').hidden=true;$('task-list').replaceChildren();feedback(e.status===404?'电脑版本仅支持只读实况，请升级后使用任务管理。':e.message);reconcileReview();}
  finally{if(gen===generation)fetching=false;}
}
async function loadTasks(){
  if(!profile?.grants.view_tasks||!active)return;
  const gen=generation;
  try{
    const data=await request('/api/v2/tasks?offset='+offset);
    if(gen!==generation||!profile?.grants.view_tasks)return;
    taskRevision=data.revision;
    const list=$('task-list');list.replaceChildren();
    for(const task of data.tasks){
      const card=el('article',undefined,'task-row');card.append(el('h3',task.title),el('p',`${task.enabled?'已开启':'已关闭'} · ${task.start_date} ${task.time_of_day} · ${task.timezone}`,'fine'));
      const cycle=task.interval_hours?`每 ${task.interval_hours} 小时`:task.weekdays.length?'每周 '+task.weekdays.map(i=>'一二三四五六日'[i]).join('、'):'单次';
      card.append(el('p',cycle+` · 提前 ${task.lead_minutes} 分钟`,'fine'),el('p',task.body.slice(0,160)+(task.body.length>160?'…':''),'summary'));
      const actions=el('div',undefined,'button-row');actions.append(button(profile.grants.edit_tasks?'查看 / 编辑':'查看详情',()=>edit(task)));
      if(profile.grants.edit_tasks)actions.append(button(task.enabled?'关闭提醒':'开启提醒',async()=>{await send('save_task',{task_id:task.id,expected_revision:task.revision,value:taskValue(task,{enabled:!task.enabled})});}));
      if(profile.grants.delete_tasks)actions.append(button('删除',()=>remove(task)));
      card.append(actions);list.append(card);
    }
    if(!data.tasks.length)list.append(el('p',offset?'本页为空，请刷新首页。':'暂无提醒任务。'));
    $('more-tasks').hidden=offset+data.tasks.length>=data.total;
    $('task-permission').textContent=`共 ${data.total} 项 · 当前 ${data.tasks.length} 项 · 日期按各任务时区解释`;
  }catch(e){feedback(e.message);if(e.status===403||e.status===401){$('task-list').replaceChildren();profile=null;}}
}
function taskValue(task,changes={}){return Object.fromEntries(['title','body','start_date','time_of_day','timezone','weekdays','interval_hours','lead_minutes','enabled'].map(k=>[k,k in changes?changes[k]:task[k]]));}
function edit(task){
  review=null;open(task?'提醒任务':'新增提醒任务');taskDialog=true;
  const allowed=profile?.grants.edit_tasks;
  const date=new Date(Date.now()+3600000);const pad=n=>String(n).padStart(2,'0');
  const title=field('标题（最多 40 字）','text',task?.title);title.maxLength=40;title.required=true;
  const body=field('内容','textarea',task?.body);body.maxLength=4000;body.rows=5;
  const start=field('起始日期','date',task?.start_date||`${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`);start.required=true;
  const time=field('提醒时间','time',task?.time_of_day||`${pad(date.getHours())}:${pad(date.getMinutes())}`);time.required=true;
  const zone=field('时区（IANA 名称）','text',task?.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone);zone.required=true;
  const label=el('label','重复方式'),mode=el('select');for(const [v,t] of [['once','单次'],['weekly','每周'],['interval','间隔小时']]){const o=el('option',t);o.value=v;mode.append(o);}label.append(mode);$('remote-fields').append(label);mode.value=task?.interval_hours?'interval':task?.weekdays?.length?'weekly':'once';
  const days=el('div',undefined,'weekdays'),week=[];for(let i=0;i<7;i++){const c=check('周'+'一二三四五六日'[i],task?.weekdays?.includes(i));week.push(c.input);days.append(c.wrap);}$('remote-fields').append(days);
  const hours=field('间隔小时（1–8760）','number',task?.interval_hours||2);hours.min=1;hours.max=8760;hours.step=1;
  const update=()=>{days.hidden=mode.value!=='weekly';hours.parentElement.hidden=mode.value!=='interval';};mode.onchange=update;update();
  const lead=field('提前上岛提醒（2–10 分钟）','number',task?.lead_minutes||5);lead.min=2;lead.max=10;lead.step=1;lead.required=true;
  const enabled=check('开启提醒',task?task.enabled:true);$('remote-fields').append(enabled.wrap);
  if(!allowed){for(const n of $('remote-fields').querySelectorAll('input,textarea,select'))n.disabled=true;$('remote-submit').hidden=true;}
  formSubmit=async()=>{
    if(!can('edit_tasks'))throw new Error('连接或编辑权限已失效');
    const weekdays=mode.value==='weekly'?week.flatMap((n,i)=>n.checked?[i]:[]):[];
    if(mode.value==='weekly'&&!weekdays.length)throw new Error('请选择至少一个星期');
    return send('save_task',{...(task?{task_id:task.id,expected_revision:task.revision}:{}),value:{title:title.value,body:body.value,start_date:start.value,time_of_day:time.value,timezone:zone.value,weekdays,interval_hours:mode.value==='interval'?Number(hours.value):0,lead_minutes:Number(lead.value),enabled:enabled.input.checked}},true);
  };
}
function remove(task){review=null;open('删除提醒任务');taskDialog=true;$('remote-fields').append(el('p',`删除“${task.title}”及其提醒实例？此操作会同步到电脑。`));$('remote-submit').textContent='确认删除';formSubmit=()=>send('delete_task',{task_id:task.id,expected_revision:task.revision},true);}
function renderActions(){
  const rows=profile?.actions||[],key=JSON.stringify(rows);
  if(key===actionKey)return;actionKey=key;
  const container=$('remote-actions');container.replaceChildren();container.hidden=!rows.length;
  if(rows.length)container.append(el('h2','需要处理'));
  for(const row of rows){const card=el('article',undefined,'task-row');card.append(el('strong',`${names[row.source]||row.source} · ${row.title}`));
    card.append(el('p',row.approval?.tool_name||row.status,'fine'));
    const actions=el('div',undefined,'button-row');
    for(const action of row.actions){if(action.type==='local_reminder'){for(const [k,t] of [['snooze','5 分钟后提醒'],['acknowledge','已知晓']])actions.append(button(t,()=>reviewAction(row,action,k)));}else actions.append(button(actionNames[action.type]||action.type,()=>reviewAction(row,action)));}
    card.append(actions);container.append(card);
  }
}
function reviewAction(row,action,reminderAction){
  if(!active)return;open(actionNames[action.type]||'处理提醒');review=row;
  $('remote-fields').append(el('p',`${names[row.source]||row.source} · ${row.title}`),el('p',row.approval?.reason||''),el('pre',row.approval?.detail||row.summary||'请确认当前操作。','approval-detail'));
  if(row.approval?.notice)$('remote-fields').append(el('p',row.approval.notice));
  const answers=[];
  if(action.type==='answer_question')for(const q of row.questions||[]){
    const box=el('fieldset');box.append(el('legend',q.question));const opts=[];
    for(const option of q.options){const c=check(option,false);box.append(c.wrap);opts.push([option,c.input]);}
    const custom=el('input');custom.type='text';custom.placeholder='自定义回答';custom.maxLength=2000;if(q.custom)box.append(custom);
    $('remote-fields').append(box);answers.push(()=>[...opts.filter(x=>x[1].checked).map(x=>x[0]),...(q.custom&&custom.value.trim()?[custom.value.trim()]:[])]);
  }
  $('remote-submit').textContent=reminderAction==='snooze'?'暂缓本次提醒':reminderAction?'知晓本次提醒':actionNames[action.type]||'提交';
  formSubmit=async()=>{
    const current=profile?.actions?.find(a=>a.id===row.id);if(!active||!current||current.revision!==row.revision)throw new Error('请求已变化，请重新打开');
    const payload=Object.fromEntries(['confirmation_id','expected_revision','cycle'].filter(k=>k in action).map(k=>[k,action[k]]));
    if(reminderAction)payload.action=reminderAction;
    if(action.type==='answer_question')payload.answers=answers.map(f=>f());
    return send(action.type==='local_reminder'?'reminder':'agent',{entity:row.id,revision:row.revision,operation:action.type,payload},true);
  };
}
async function send(operation,payload,inDialog=false){
  const blocked=(!active||busy||!profile)?'请等待连接恢复或当前操作完成':pendingCommand?'上次操作结果未确认，请先核对，不能再次提交新操作。':'';
  if(blocked){feedback(blocked);if(inDialog)throw new Error(blocked);return false;}
  busy=true;const id=crypto.randomUUID().replaceAll('-',''),gen=generation;
  try{
    pending(id);
    const result=await request('/api/v2/commands',{method:'POST',headers:{'Content-Type':'application/json','X-Solo-CSRF':profile.csrf},body:JSON.stringify({id,operation,payload}),signal:AbortSignal.timeout(15000)});
    if(gen!==generation)return false;
    feedback(result.message);
    if(result.status!=='outcome_unknown')pending('');
    if(result.status==='rejected')throw new Error(result.message);
    if(result.status==='outcome_unknown')throw new Error(result.message+'；请核对上次操作结果');
    taskRevision=-1;return true;
  }catch(e){if(gen===generation){if([400,401,403,404,413,429].includes(e.status))pending('');feedback(e.message);if(inDialog)throw e;}return false;}
  finally{busy=false;if(gen===generation){await refresh();if(taskPage)await loadTasks();}}
}
$('remote-close').onclick=close;
$('remote-dialog').addEventListener('cancel',e=>{if(busy)e.preventDefault();else review=null;});
$('remote-form').onsubmit=async e=>{e.preventDefault();if(!formSubmit||busy)return;$('remote-submit').disabled=true;try{if(await formSubmit())close();}catch(error){$('remote-form-error').textContent=error.message;}finally{$('remote-submit').disabled=false;reconcileReview();}};
function select(page){taskPage=page;$('live-page').hidden=page;$('tasks-page').hidden=!page;$('tab-live').setAttribute('aria-pressed',String(!page));$('tab-tasks').setAttribute('aria-pressed',String(page));if(page){offset=0;refresh().then(loadTasks);}}
$('tab-live').onclick=()=>select(false);$('tab-tasks').onclick=()=>select(true);
$('load-tasks').onclick=()=>{offset=0;loadTasks();};$('more-tasks').onclick=()=>{offset+=50;loadTasks();};$('new-task').onclick=()=>edit(null);
pending(pendingCommand);
$('check-command').onclick=async()=>{const id=pendingCommand;if(!id)return;try{const r=await request('/api/v2/commands/'+id);feedback(r.message);if(r.status!=='outcome_unknown')pending('');}catch(e){feedback(e.status===404?'电脑未找到回执，请先核对任务实际状态。':e.message);}};
$('resolve-command').onclick=()=>{if(busy)return;if(window.confirm('仅在已检查电脑或来源应用的实际结果后继续。解除等待不会撤回之前的请求；再次新建或执行可能产生重复结果。确认已核对？')){pending('');feedback('已解除等待，请根据实际结果决定下一步。');}};
window.addEventListener('solo-service-changed',()=>refresh());
setInterval(()=>{if(active&&Date.now()-lastContact>12000)remote.disconnected();else if(active&&Date.now()-lastFetch>10000)refresh();},2000);
if(location.protocol!=='https:')$('transport-note').textContent='当前为 HTTP 兼容只读连接，不开放任务编辑或 Agent 操作。请在电脑启用 HTTPS。';
export const remote={
  connected(){lastContact=Date.now();if(!active){active=true;refresh();}},
  disconnected(){active=false;generation++;fetching=false;reconcileReview();},
  clear(){active=false;generation++;fetching=false;profile=null;taskRevision=-1;actionKey='';$('task-list').replaceChildren();$('remote-actions').replaceChildren();$('remote-actions').hidden=true;$('remote-dialog').close();review=null;}
};
