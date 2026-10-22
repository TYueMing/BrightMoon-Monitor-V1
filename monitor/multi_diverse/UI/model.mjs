/** Pure presentation policy. No networking, Agent calls, or persistence. */
export function layoutFor(width, domainCount, stage=0, remoteSelected=false) {
  // Remote header and detail card share one width in every stage.
  // Local-only mode retains the existing connected silhouette.
  const total = Math.min(domainCount || stage ? 420 : 300, width-32);
  const headerWidth = total;
  const circle = 62, gap = domainCount ? 10 : 0;
  const long = headerWidth - (domainCount ? circle + gap : 0);
  const detailGap = domainCount && stage ? 12 : 0;
  return {total, headerWidth, detailGap, detailTop:62+detailGap,
    layout:domainCount ? 'remote-split' : 'local-connected',
    circle, gap, long, local: remoteSelected && domainCount ? circle : long,
    remote: domainCount ? (remoteSelected ? long : circle) : 0,
    left:(width-total)/2, detailHeight:stage === 2 ? 518 : 182};
}
export function nextSelection(current, target, stage) {
  return {selected:target,stage:current === target ? (stage ? 0 : 1) : stage};
}
export function presentDomains(domains, hidden = new Set()) {
  // Stable supplied order; polling does not reshuffle an in-flight click target.
  return domains.filter(d => d.count > 0 && !hidden.has(d.id));
}
export function selectionAfterRemoval(selected, domains) {
  return selected === 'local' || domains.some(d => d.id === selected) ? selected : 'local';
}
export const fixtures = {
  mixed: {label:'本机 + 远端', local:true, domains:[
    {id:'laptop',kind:'computer',name:'设计笔记本',source:'Codex · 远端电脑',count:2,status:'running',state:'2 项执行中',title:'组件库适配与验证',summary:'正在核对移动端布局，并验证组件库在不同窗口尺寸下的表现。',progress:2,steps:['读取组件','调整布局','运行测试','整理结果'],metrics:[['已完成步骤','2 / 4'],['测试结果','18 项通过'],['最近同步','刚刚']],elapsed:'03:24'},
    {id:'build',kind:'build',name:'构建服务',source:'CI · 独立领域',count:1,status:'attention',state:'等待人工确认',title:'预览构建等待确认',summary:'测试与静态检查已完成。构建服务正在等待来源平台上的发布确认。',progress:2,steps:['安装依赖','测试检查','人工确认','发布预览'],metrics:[['构建编号','#128'],['测试结果','42 项通过'],['访问权限','仅查看']],elapsed:'01:36'}]},
  overflow: {label:'多个领域',local:true,domains:[]},
  offline: {label:'来源失联',local:true,domains:[{id:'laptop',kind:'computer',name:'设计笔记本',source:'Codex · 远端电脑',count:2,status:'offline',state:'连接中断',title:'来源暂时离线',summary:'保留最后一次收到的状态。连接中断不代表任务失败；操作暂不可用。',progress:2,steps:['读取组件','调整布局','运行测试','整理结果'],metrics:[['最后同步','30 秒前'],['执行时长','已冻结'],['展示策略','失联后 60 秒退场']],elapsed:'03:24'}]},
  remote: {label:'仅有远端任务',local:false,domains:[]},
  empty: {label:'无外部信息',local:true,domains:[]}
};
fixtures.overflow.domains = [...fixtures.mixed.domains,
 {id:'board',kind:'device',name:'开发板',source:'设备 · 独立领域',count:1,status:'running',state:'正在采集',title:'传感器采集正常',summary:'设备正在持续采集数据。此来源没有步骤计划，因此不绘制虚假的任务进度条。',progress:null,steps:[],metrics:[['采集速率','10 Hz'],['温度','36.2 °C'],['连接质量','正常']],elapsed:'12:08'},
 {id:'home',kind:'computer',name:'家用电脑',source:'OpenCode · 远端电脑',count:1,status:'done',state:'执行完成',title:'回归验证已完成',summary:'本轮验证已结束，来源报告所有检查通过。卡片按照本机设置保留后撤下。',progress:4,steps:['检查','修改','测试','汇总'],metrics:[['检查结果','24 项通过'],['文件变更','3 个文件']],elapsed:'05:18'}];
fixtures.remote.domains = fixtures.mixed.domains.slice(0,1);
export const localTask = {id:'local',kind:'agent',name:'本机',source:'Codex · 这台电脑',count:1,status:'running',state:'执行中',title:'整理项目测试',summary:'正在完善监控模块的测试，检查任务状态与界面同步是否一致。',progress:2,steps:['阅读代码','补充测试','执行验证','整理结果'],metrics:[['当前步骤','执行验证'],['已完成','2 / 4'],['文件变更','3 个文件']],elapsed:'00:42'};
