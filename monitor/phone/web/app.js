import {request,Transport} from './transport.js';
const $=id=>document.getElementById(id);
const labels={running:'执行中',waiting_model:'等待模型',waiting_confirmation:'待授权',waiting_input:'待补充信息',completed:'执行完成',failed:'执行失败',cancelled:'已停止',interrupted:'已中断',partial:'部分完成',blocked:'执行受阻',budget_exhausted:'预算已用尽',paused:'已暂停',queued:'等待调度',cancellation_requested:'正在停止',stale:'监控失联',error:'连接异常',reminding:'待办提醒',unknown:'待核对',waiting_lock:'等待资源'};
const names={codex:'Codex',opencode:'OpenCode',deepseek_harness:'Harness',solo_task:'待办'};
let model=null,focus='',stage=0,more=false,epoch='',revision=-1,lastSeen=0,age=null,serverTime=0,online=false,bodyKey='',stripKey='',attentionLatch='',gesture=null,cleared=false,appearanceKey='';
try{const saved=JSON.parse(sessionStorage.getItem('solo-view')||'{}');epoch=saved.epoch||'';focus=saved.focus||'';stage=[0,1,2].includes(saved.stage)?saved.stage:0;}catch{}
function node(tag,content,cls){const n=document.createElement(tag);if(content!==undefined)n.textContent=String(content);if(cls)n.className=cls;return n;}
function persist(){try{sessionStorage.setItem('solo-view',JSON.stringify({focus,stage,epoch}));}catch{}}
function current(){return model?.tasks?.find(t=>t.task_key===focus);}
function color(task){if(task.connection_state==='error'||['failed','error','stale'].includes(task.status))return '#ee7583';if(['waiting_confirmation','waiting_input','paused','reminding'].includes(task.status))return '#eab45b';if(task.status==='completed')return '#49c795';return '#649cf0';}
function showPair(message=''){appearanceKey='';clearPageTheme();cleared=false;transport.stop();model=null;focus='';revision=-1;epoch='';bodyKey=stripKey='';persist();$('viewer').hidden=true;$('pairing').hidden=false;$('pair-error').textContent=message;$('connection').textContent='尚未连接';$('card-body').replaceChildren();$('task-strip').replaceChildren();$('island').style.removeProperty('--wallpaper');for(const id of ['pendant','idle-image']){$(id).removeAttribute('src');$(id).hidden=true;}}
function heartbeat(value){lastSeen=performance.now();age=value.producer_age_ms;serverTime=value.server_time_ms;online=true;if(cleared&&age!==null&&age<5000){cleared=false;transport.start();return;}updateTime();}
function accept(value){
  if(value.schema_version!==1){showPair('页面与电脑版本不兼容，请更新后重试。');return;}
  if(value.epoch!==epoch){epoch=value.epoch;revision=-1;focus='';stage=0;attentionLatch='';bodyKey=stripKey='';}
  if(value.revision<revision)return;
  cleared=false;revision=value.revision;const previous=model?.tasks?.length||0;model=value;heartbeat(value);
  $('pairing').hidden=true;$('viewer').hidden=false;
  if(!model.tasks.some(t=>t.task_key===focus)||$('follow').checked)focus=model.desktop_focus_key||model.tasks[0]?.task_key||'';
  if(!model.tasks.some(t=>t.task_key===focus))focus=model.tasks[0]?.task_key||'';
  if(previous&&!model.tasks.length){stage=0;attentionLatch='';}
  focusAttention();
  persist();render();
}
const transport=new Transport(accept,heartbeat,()=>{online=false;updateTime();},()=>showPair('连接已过期或被电脑撤销，请重新配对。'));
// Hold the first unresolved request; manual task selection remains independent.
function focusAttention(){
  if(stage===0||!model)return;
  const queue=model.tasks.filter(t=>t.attention&&t.connection_state!=='error')
    .sort((a,b)=>a.attention.requested_at_ms-b.attention.requested_at_ms);
  const head=queue.find(t=>t.attention.id===attentionLatch)||queue[0];
  if(head&&head.attention.id!==attentionLatch){focus=head.task_key;attentionLatch=head.attention.id;}
  if(!head)attentionLatch='';
}
function setStage(next){stage=next;if(!stage)attentionLatch='';else focusAttention();persist();render();}
function imageURL(id){return typeof id==='string'&&/^[a-f0-9]{64}$/.test(id)?'/api/v1/assets/'+id:'';}
function clearPageTheme(){
  const root=document.documentElement;
  for(const property of ['--page-wallpaper','--panel-color','--panel-alpha','--page-ink'])root.style.removeProperty(property);
  root.classList.remove('has-wallpaper');
}
function applyPageTheme(appearance){
  // Only resource/theme changes touch image CSS. Heartbeats and task updates
  // must never assign a new image URL or download the wallpaper again.
  const key=JSON.stringify(appearance);if(key===appearanceKey)return;appearanceKey=key;
  const images=appearance.images||{},wallpaper=imageURL(images.wallpaper||images.page_wallpaper);
  const page=imageURL(images.page_wallpaper||images.wallpaper),root=document.documentElement;
  if(page)root.style.setProperty('--page-wallpaper',`url("${page}")`);else root.style.removeProperty('--page-wallpaper');
  root.classList.toggle('has-wallpaper',!!page);
  root.style.setProperty('--panel-color',appearance.panel_color||'#ffffff');
  root.style.setProperty('--panel-alpha',appearance.panel_alpha??.5);
  const panel=appearance.panel_color||'#ffffff';
  const light=(.2126*parseInt(panel.slice(1,3),16)+.7152*parseInt(panel.slice(3,5),16)+.0722*parseInt(panel.slice(5,7),16))>145;
  root.style.setProperty('--page-ink',light?'#243349':'#edf3fc');
  if(wallpaper)$('island').style.setProperty('--wallpaper',`url("${wallpaper}")`);else $('island').style.removeProperty('--wallpaper');
}
function render(){
  if(!model)return;const task=current(),tasks=model.tasks||[];const island=$('island');island.dataset.stage=String(stage);$('title-toggle').setAttribute('aria-expanded',String(stage>0));$('card-body').hidden=stage===0;$('card-controls').hidden=stage===0;$('details').textContent=stage===1?'查看详情':'返回进度';
  const appearance=model.appearance||{},bg=appearance.background_color||'#141d2c';island.style.setProperty('--island-color',bg);island.style.setProperty('--background-alpha',Math.max(.45,appearance.background_alpha??1));
  const light=/^#[a-f0-9]{6}$/i.test(bg)&&(.2126*parseInt(bg.slice(1,3),16)+.7152*parseInt(bg.slice(3,5),16)+.0722*parseInt(bg.slice(5,7),16))>145;
  island.style.setProperty('--ink',light?'#172a42':'#eff4ff');island.style.setProperty('--muted',light?'#425d79':'#acbad0');island.style.setProperty('--readable',light?'rgba(255,255,255,.6)':'rgba(10,18,32,.5)');
  const images=appearance.images||{};applyPageTheme(appearance);
  const caption=task?(stage===0?(tasks.length===1?task.title:`${tasks.length} 项任务`):`${task.source} · ${labels[task.status]||'待核对'}`):model.loading?'正在加载…':model.idle?.upcoming_count?`${model.idle.upcoming_count} 项提醒`:'Bright Moon';
  $('capsule-title').textContent=caption;
  const key=JSON.stringify([tasks.map(t=>[t.task_key,t.status,t.connection_state]),focus,more,stage]);
  if(key!==stripKey){stripKey=key;const strip=$('task-strip');strip.replaceChildren();const list=tasks.length?tasks:[{app_id:'unknown',task_key:'idle',source:'Bright Moon',status:'completed'}];const visible=stage||more||tasks.some(t=>t.app_id==='solo_task')?list:list.slice(0,3);for(const t of visible){const button=node('button',undefined,'task-icon'+(t.task_key===focus||t.task_key==='idle'?' selected':''));button.setAttribute('aria-label',`${t.source}：${t.task_key==='idle'?'空闲':labels[t.status]||'待核对'}`);button.setAttribute('aria-pressed',String(t.task_key===focus));button.style.setProperty('--status',color(t));const ring=node('span',undefined,'ring');if(['completed','failed','cancelled','interrupted'].includes(t.status)&&t.task_key!=='idle')ring.append(node('span',t.status==='completed'?'':t.status==='failed'?'!':'■','symbol'+(t.status==='completed'?' check':'')));else{const img=node('img');img.src='/icons/'+(names[t.app_id]?t.app_id:'unknown');img.alt='';ring.append(img);}button.append(ring);button.onclick=()=>{if(t.task_key==='idle')setStage((stage+1)%3);else{focus=t.task_key;persist();render();}};strip.append(button);}if(!stage&&!more&&tasks.length>3&&!tasks.some(t=>t.app_id==='solo_task')){const button=node('button','…','task-icon more');button.setAttribute('aria-label','显示所有任务');button.onclick=()=>{more=true;render();};strip.append(button);}}
  const contentKey=JSON.stringify([task,stage,model.idle,model.loading]);
  if(contentKey!==bodyKey){bodyKey=contentKey;const body=$('card-body');const scroll=window.scrollY;body.replaceChildren();if(task){
    body.append(node('p','任务摘要','section-title'),node('p',task.summary||(model.privacy_mode==='status_only'?'正文已在电脑端隐藏。':'来源尚未提供摘要。'),'summary'));
    const plan=task.chain||{},all=plan.nodes||[];if(all.length){let active=all.findIndex(s=>['running','waiting_model'].includes(s.status));if(active<0)active=0;const start=Math.min(Math.max(0,active-2),Math.max(0,all.length-6));const chain=node('div',undefined,'chain');chain.setAttribute('aria-label',plan.label);all.slice(start,start+6).forEach((item,index)=>{if(index)chain.append(node('span',undefined,'chain-line'));const circle=node('span',undefined,'node '+item.status);circle.title=item.label+' · '+(labels[item.status]||item.status);chain.append(circle);});body.append(chain);if(all.length>6)body.append(node('p',`步骤 ${start+1}–${Math.min(start+6,all.length)} / 共 ${plan.total} 步`,'chain-label'));}
    body.append(node('p',(plan.label||'未报告进度')+(plan.completed!==null&&plan.completed!==undefined?` ${plan.completed}/${plan.total}`:'')+(plan.truncated?' · 列表已截断':''),'chain-label'));
    const activeStep=all.find(s=>['running','waiting_model','waiting_confirmation'].includes(s.status));if(activeStep)body.append(node('p','当前步骤：'+activeStep.label,'chain-label'));
    const recentTools=task.detail_blocks?.find(b=>b.type==='tool_list')?.items;if(stage===1&&recentTools?.length){const recent=recentTools.at(-1);body.append(node('p','最近工具：'+recent.name+' · '+(labels[recent.status]||recent.status),'chain-label'));}
    if(task.attention){const notice=node('div',undefined,'attention');notice.append(node('strong',task.attention.title),node('p',task.attention.reason),node('p',task.attention.notice));body.append(notice);}
    if(stage===2){if(all.length){body.append(node('p','执行步骤','section-title'));const list=node('ul',undefined,'detail-list');all.forEach(s=>list.append(node('li',`${labels[s.status]||s.status} · ${s.label}`)));body.append(list);}for(const block of task.detail_blocks||[]){body.append(node('p',block.title,'section-title'));if(block.type==='metric_list'){for(const item of block.items){const row=node('div',undefined,'metric');row.append(node('span',item.label),node('span',item.value));body.append(row);}}else if(block.type==='tool_list'){const list=node('ul',undefined,'detail-list');for(const item of block.items)list.append(node('li',`${item.name} · ${labels[item.status]||item.status}${item.exit_code!==null?' · 退出码 '+item.exit_code:''}\n${item.detail}`));body.append(list);}else if(block.type==='text')body.append(node('p',block.text,'summary'));}if(task.truncated)body.append(node('p','内容较长，部分详情已省略。','chain-label'));}
    if(task.app_id==='solo_task'){if(task.reminder_due_at_ms)body.append(node('p','计划时间：'+new Date(task.reminder_due_at_ms).toLocaleString(),'chain-label'));body.append(node('p','忽略或已知晓，请在电脑端处理。','chain-label'));}
  }else{body.append(node('h2',model.loading?'正在接收桌面状态…':'忙完了，给自己一点空闲。','idle-text'));const idle=model.idle||{},next=idle.next_reminder;body.append(node('p',idle.error?'电脑待办数据暂时不可用。':!idle.preview_enabled?'未开启空闲预告；有任务时会自动出现。':idle.upcoming_count?`未来 1 小时内有 ${idle.upcoming_count} 项提醒。`:'未来 1 小时暂无提醒。','idle-note'));if(next){body.append(node('p',next.title,'summary'),node('p',next.body,'idle-note'),node('p','开始提醒 '+new Date(next.remind_at_ms).toLocaleString(),'chain-label'));}else body.append(node('p','安心享受这一刻。','idle-note'));}
    window.scrollTo(0,scroll);
  }
  for(const [id,asset,visible] of [['pendant',images.rope,stage===0],['idle-image',images.cartoon,!task&&stage>0]]){const element=$(id),url=imageURL(asset);element.hidden=!url||!visible;if(url&&element.getAttribute('src')!==url)element.src=url;if(!url)element.removeAttribute('src');}
  updateTime();
}
function updateTime(){if(!model)return;const elapsed=performance.now()-lastSeen;const stale=age===null?Infinity:age+elapsed;const disconnected=!online||elapsed>25000;const delayed=stale>5000;const warning=disconnected?'与电脑连接中断，显示上次已知状态。':stale>15000?'电脑监控刷新暂停，以下不是实时状态。':delayed?'电脑状态更新延迟…':'';$('connection').textContent=disconnected?'连接中断':delayed?'刷新滞后':'● 已连接';$('warning').hidden=!warning;$('warning').textContent=warning;$('island').classList.toggle('offline',disconnected||delayed);$('freshness').textContent=`最近同步：${Math.floor(elapsed/1000)} 秒前 · ${model.privacy_mode==='status_only'?'仅状态':'状态与摘要'}`;const task=current();if(task?.timer?.started_at_ms){const t=task.timer;const end=t.final?(t.ended_at_ms||t.started_at_ms):((disconnected||delayed)?serverTime-(age||0):serverTime+elapsed);const seconds=Math.max(0,Math.floor((end-t.started_at_ms)/1000));$('timer').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;$('timer').title=t.basis==='observed'?'从首次观察开始计时':'来源报告的开始时间';}else $('timer').textContent=task?'--:--':'空闲';if((disconnected&&elapsed>60000)||(delayed&&stale>60000)){cleared=true;model.tasks=[];model.idle={};$('card-body').replaceChildren(node('p','连接恢复后重新同步，旧任务内容已清空。','idle-note'));$('task-strip').replaceChildren();$('capsule-title').textContent='等待恢复';$('timer').textContent='--:--';for(const id of ['pendant','idle-image']){$(id).hidden=true;$(id).removeAttribute('src');}$('island').style.removeProperty('--wallpaper');clearPageTheme();appearanceKey='';bodyKey=stripKey='';}}
$('pair-form').onsubmit=async event=>{event.preventDefault();$('pair-button').disabled=true;$('pair-error').textContent='';try{await request('/api/v1/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:$('code').value,name:$('device').value})});$('code').value='';await transport.start();}catch(error){$('pair-error').textContent=error.message;}finally{$('pair-button').disabled=false;}};
$('logout').onclick=async()=>{try{await request('/api/v1/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});showPair();}catch{transport.stop();showPair('暂时无法通知电脑退出，请在电脑端撤销此设备。');}};
$('title-toggle').onclick=()=>setStage((stage+1)%3);$('collapse').onclick=()=>setStage(0);$('details').onclick=()=>setStage(stage===1?2:1);$('follow').onchange=()=>{if($('follow').checked&&model){focus=model.desktop_focus_key;render();}};
const island=$('island');island.addEventListener('pointerdown',event=>gesture={x:event.clientX,y:event.clientY});island.addEventListener('click',event=>{if(event.target.closest('button,a,input,label,select')||!gesture||Math.hypot(event.clientX-gesture.x,event.clientY-gesture.y)>8||window.getSelection()?.toString())return;setStage((stage+1)%3);});
$('pendant').onclick=()=>setStage(1);$('pendant').onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setStage(1);}};
document.addEventListener('visibilitychange',()=>{if(document.hidden)transport.stop();else if(!$('viewer').hidden)transport.start();});
window.addEventListener('pagehide',()=>transport.stop());window.addEventListener('pageshow',event=>{if(event.persisted&&!$('viewer').hidden)transport.start();});
setInterval(updateTime,1000);
request('/api/v1/session').then(()=>transport.start()).catch(()=>showPair());
