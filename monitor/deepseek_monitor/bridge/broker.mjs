/** Versioned, narrow adapter for Harness 0.2.0-rc.2's existing Remote gateway. */
import {createHash, randomUUID} from 'node:crypto';

const clone = value => JSON.parse(JSON.stringify(value));
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const questionIdentity = questions => JSON.stringify(questions.map(q => ({id:q.id, question:q.question,
  header:q.header || '', options:q.options || [], multiSelect:q.multiSelect ?? q.multi_select ?? false})));
const empty = {async *[Symbol.asyncIterator]() {}};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
export class BridgeError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
const fail = (code, message) => { throw new BridgeError(code, message); };

export function validateAnswers(questions, value) {
  if (!value || Object.keys(value).some(k => k !== 'answers') || !Array.isArray(value.answers)
      || value.answers.length !== questions.length) fail('BAD_ANSWER', '请完成本组全部问题');
  const seen = new Set();
  const answers = value.answers.map(answer => {
    const question = questions.find(q => q.id === answer?.id);
    if (!question || seen.has(answer.id) || Object.keys(answer).some(k => !['id','selected','custom'].includes(k)))
      fail('BAD_ANSWER', '答案与当前问题不匹配');
    seen.add(answer.id);
    if (!Array.isArray(answer.selected) || answer.selected.some(s => typeof s !== 'string')
        || new Set(answer.selected).size !== answer.selected.length)
      fail('BAD_ANSWER', '选项格式无效');
    const options = new Set((question.options || []).map(o => o.label));
    if (answer.selected.some(s => !options.has(s)) || (!question.multiSelect && answer.selected.length > 1))
      fail('BAD_ANSWER', '请选择当前问题提供的有效选项');
    if (answer.custom !== undefined && (typeof answer.custom !== 'string' || answer.custom.length > 16000))
      fail('BAD_ANSWER', '文字答案过长或格式无效');
    // A skipped answer must be an explicit user action; this UI requires a response.
    if (!answer.selected.length && !(answer.custom || '').trim()) fail('BAD_ANSWER', '请填写答案或选择选项');
    return {id: answer.id, selected: [...answer.selected], ...(answer.custom?.trim() ? {custom: answer.custom} : {})};
  });
  return {answers};
}

function eventsOf(agent) { return agent?.session?.snapshotEvents?.() || []; }
function metadata(agent, request = {}) {
  const events = eventsOf(agent);
  const last = type => events.findLast(e => e.type === type);
  const callId = request.callId || request.wait?.callId || '';
  const tool = callId ? events.findLast(e => e.type === 'tool/call' && e.data.callId === callId) : undefined;
  let args = {};
  try { args = tool ? JSON.parse(tool.data.arguments) : {}; } catch { args = {说明: '参数无法解析，请核对 Harness 原窗口'}; }
  const asked = last('approval/asked');
  const audit = asked && asked.data.toolName === request.toolName && (asked.data.callId || '') === callId ? asked : undefined;
  return {
    session_id: String(agent.id), call_id: callId,
    title: String(last('session/title')?.data.title || agent.session.header?.cwd || 'DeepSeek Harness'),
    project: String(agent.session.header?.cwd || ''), turn: last('turn/start')?.data.turn ?? 0,
    arguments: args, audit_id: audit?.data.id || '', audit_seq: audit?.seq ?? -1,
  };
}

export class InteractionBroker {
  constructor(ctx, home) {
    this.ctx = ctx;
    this.home = home;
    this.instance = randomUUID();
    this.clientId = '';
    this.connected = false;
    this.requests = new Map();
    this.receipts = new Map();
    this.holds = new Map();
    this.clients = new Map();
    this.controller = new AbortController();
    this.closed = false;
    this.generation = 0;
    this.sequence = 0;
  }
  start() { this.worker = this.consume(); return this; }
  supported() {
    const gateway = this.ctx.typertGateway;
    return typeof gateway?.wireStream?.open === 'function' && typeof gateway.dispatchRpc === 'function'
      && gateway.pendingRemoteEvents instanceof Map && typeof this.ctx.agents?.get === 'function'
      && typeof this.ctx.agents?.roots === 'function' && typeof this.ctx.userQuestions?.answer === 'function';
  }
  async consume() {
    while (!this.closed) {
      try {
        if (!this.supported()) throw new Error('unsupported gateway');
        const signal = this.controller.signal;
        const stream = await this.ctx.typertGateway.wireStream.open('$events', {args: {}}, empty, undefined, signal);
        for await (const frame of stream) {
          if (this.closed) break;
          await this.receive(frame);
        }
      } catch { /* startup ordering and transport loss both reconnect without replaying decisions */ }
      this.connected = false;
      this.clientId = '';
      this.requests.clear();
      this.releaseHolds();
      if (!this.closed) await sleep(500);
    }
  }
  async receive(frame) {
    if (frame.type === 'ready') {
      this.clientId = frame.clientId;
      this.connected = true;
      this.generation++;
      return;
    }
    if (frame.type === 'cancel') {
      this.requests.delete(frame.eventId);
      this.releaseHold(frame.eventId);
      return;
    }
    if (frame.type !== 'waterfall') return;
    if (!['approval/request', 'user-questions/request'].includes(frame.event)) {
      await this.result(frame.eventId, {kind: 'next'});
      return;
    }
    const agent = this.ctx.agents.get(frame.agentId);
    if (!agent || (frame.event === 'user-questions/request' && !this.ctx.agents.roots().includes(agent))) {
      await this.result(frame.eventId, {kind: 'next'});
      return;
    }
    const kind = frame.event === 'approval/request' ? 'approval' : 'question';
    const request = frame.request;
    if (kind === 'question' && (!Array.isArray(request.questions) || !request.questions.length)) {
      await this.result(frame.eventId, {kind: 'next'});
      return;
    }
    const meta = metadata(agent, request);
    // Legacy blocking ask_user_question does not carry a callId. Correlate only
    // an exact question schema, never just the tool name or a UI list position.
    if (kind === 'question' && !meta.call_id) {
      const calls = eventsOf(agent).filter(e => e.type === 'tool/call' && e.data.name === 'ask_user_question');
      const finished = new Set(eventsOf(agent).filter(e => e.type === 'tool/result').map(e => e.data.message?.toolCallId));
      const matches = calls.filter(e => {
        try { return !finished.has(e.data.callId) && questionIdentity(JSON.parse(e.data.arguments).questions) === questionIdentity(request.questions); }
        catch { return false; }
      });
      if (matches.length === 1) meta.call_id = matches[0].data.callId;
    }
    const item = {
      id: frame.eventId, kind, mode: 'live', ...meta,
      tool_name: request.toolName || 'ask_user_question', reason: request.reason || '',
      questions: kind === 'question' ? clone(request.questions) : [],
      timed: request.wait?.timed === true, requested_at: new Date().toISOString(),
      revision: `${this.generation}:${digest(request)}`, order: ++this.sequence,
    };
    this.requests.set(item.id, item);
  }
  async result(eventId, outcome) {
    const response = await this.ctx.typertGateway.dispatchRpc('$events/result', {args: {
      clientId: this.clientId, eventId, outcome,
    }}, this.controller.signal);
    if (!response.ok) fail('GATEWAY_ERROR', 'Harness 未接受交互结果');
  }
  questionState(agent) {
    return this.ctx.sessionProjections?.stateOf(agent.session, 'userQuestions')?.questions || {active: [], settled: []};
  }
  queued(agent, callId) {
    return this.ctx.userQuestions.queuedReplies?.get(agent.session)?.has(callId) || [...(agent.inbox?.nextTurn || []), ...(agent.inbox?.nextStep || [])]
      .some(m => m.source?.kind === 'user-question-reply' && m.source.callId === callId);
  }
  refresh() {
    if (!this.connected) return;
    const continued = new Set();
    for (const agent of this.ctx.agents.roots()) {
      const state = this.questionState(agent);
      for (const question of state.active || []) {
        if (question.state !== 'continued' || this.queued(agent, question.callId)) continue;
        if ([...this.requests.values()].some(r => r.session_id === agent.id && r.call_id === question.callId && r.mode === 'live')) continue;
        const id = `continued:${agent.id}:${question.callId}`;
        continued.add(id);
        const revision = `continued:${digest(question.questions)}`;
        if (this.requests.get(id)?.revision === revision) continue;
        this.requests.set(id, {
          id, kind: 'question', mode: 'continued', ...metadata(agent, {callId: question.callId}),
          tool_name: 'ask_user_question', reason: '此问题仍可补充回答', questions: clone(question.questions),
          timed: false, requested_at: new Date().toISOString(), revision, order: ++this.sequence,
        });
      }
    }
    for (const [id, item] of this.requests) {
      if ((item.mode === 'continued' && !continued.has(id)) || (item.mode === 'live' && !this.ctx.typertGateway.pendingRemoteEvents.has(id))) {
        this.requests.delete(id);
        this.releaseHold(id);
      }
    }
    for (const receipt of this.receipts.values()) {
      if (!['submitted', 'queued'].includes(receipt.status)) continue;
      const agent = this.ctx.agents.get(receipt.session_id);
      if (!agent) { receipt.status = 'unknown'; continue; }
      if (receipt.kind === 'approval') {
        const decided = eventsOf(agent).findLast(e => e.type === 'approval/decided' && e.data.id === receipt.audit_id);
        if (decided) {
          receipt.status = decided.data.outcome === receipt.outcome ? 'confirmed' : 'resolved_elsewhere';
          receipt.message = receipt.status === 'confirmed' ? 'Harness 已确认授权结果' : '该请求已在其他窗口处理';
        }
      } else {
        const settled = this.questionState(agent).settled?.find(q => q.callId === receipt.call_id);
        const result = eventsOf(agent).findLast(e => e.type === 'tool/result' && e.data.message?.toolCallId === receipt.call_id);
        const reply = eventsOf(agent).findLast(e => e.type === 'user/message' && e.data.source?.kind === 'user-question-reply' && e.data.source.callId === receipt.call_id);
        if (settled || reply || (result && !result.data.message?.isError && !result.data.error && receipt.mode === 'live')) {
          receipt.status = 'confirmed'; receipt.message = 'Harness 已收到回答';
        } else if (receipt.status === 'queued' && !this.queued(agent, receipt.call_id)) {
          receipt.status = 'discarded'; receipt.message = '排队的回答已撤回，可以重新提交';
        }
      }
    }
    const now = Date.now();
    for (const [id, lease] of this.clients) if (lease.until < now) this.clients.delete(id);
    const held = new Set([...this.clients.values()].flatMap(c => c.holds));
    for (const id of this.holds.keys()) if (!held.has(id)) this.releaseHold(id);
    for (const id of held) this.hold(id);
    for (const [id, receipt] of this.receipts) if (now - receipt.at > 300000) this.receipts.delete(id);
  }
  snapshot(client = '', holds = []) {
    if (client) this.clients.set(client, {holds: holds.slice(0, 20), until: Date.now() + 3000});
    this.refresh();
    return {protocol: 1, instance: this.instance, generation: this.generation, connected: this.connected,
      sessions: this.connected ? this.ctx.agents.roots().map(a => String(a.id)) : [],
      requests: clone([...this.requests.values()].sort((a,b) => a.order-b.order)),
      receipts: clone([...this.receipts.values()].map(({fingerprint, ...rest}) => rest))};
  }
  async respond(command) {
    if (!command || !/^[a-zA-Z0-9-]{8,80}$/.test(command.command_id || '')) fail('BAD_COMMAND', '命令标识无效');
    const fingerprint = digest(command);
    const previous = this.receipts.get(command.command_id);
    if (previous) {
      if (previous.fingerprint !== fingerprint) fail('DUPLICATE_COMMAND', '重复命令的内容不一致');
      const {fingerprint: _, ...receipt} = previous;
      return clone(receipt);
    }
    if (!this.connected || command.instance !== this.instance || command.generation !== this.generation)
      fail('DISCONNECTED', '连接已更新，请等待重新同步');
    this.refresh();
    const item = this.requests.get(command.request_id);
    if (!item || item.revision !== command.revision || item.session_id !== command.session_id)
      fail('STALE_REQUEST', '该请求已处理或已失效');
    const agent = this.ctx.agents.get(item.session_id);
    if (!agent || (item.kind === 'question' && !this.ctx.agents.roots().includes(agent))) fail('STALE_REQUEST', '任务已不再接受交互');
    let value;
    if (item.kind === 'approval') {
      if (!['approve','deny'].includes(command.action)) fail('BAD_ACTION', '授权操作无效');
      value = command.action === 'approve' ? 'allowed-once' : 'rejected';
    } else {
      if (command.action !== 'answer') fail('BAD_ACTION', '答题操作无效');
      value = validateAnswers(item.questions, command.answer);
    }
    // Install the command ID before awaiting so double-clicks cannot race.
    const receipt = {command_id: command.command_id, request_id: item.id, session_id: item.session_id,
      call_id: item.call_id, audit_id: item.audit_id, kind: item.kind, mode: item.mode,
      status: 'submitted', message: '已发送，等待 Harness 确认', at: Date.now(), fingerprint,
      ...(item.kind === 'approval' ? {outcome: value} : {})};
    this.receipts.set(command.command_id, receipt);
    if (this.receipts.size > 1000) this.receipts.delete(this.receipts.keys().next().value);
    try {
      if (item.mode === 'continued') {
        if (!this.ctx.userQuestions.answer(agent, item.call_id, value)) fail('STALE_REQUEST', '问题已不再接受回答');
        receipt.status = 'queued'; receipt.message = '回答已入队，等待 Harness 接收';
      } else {
        // The gateway deliberately acknowledges late results as no-ops. Check
        // its live delivery in the same JS turn before invoking that method.
        const pending = this.ctx.typertGateway.pendingRemoteEvents.get(item.id);
        const client = this.ctx.typertGateway.remoteEventClients.get(this.clientId);
        if (!pending?.deliveries.has(client)) fail('STALE_REQUEST', '该请求已在其他窗口处理');
        await this.result(item.id, {kind: 'result', value});
      }
      this.requests.delete(item.id);
      this.releaseHold(item.id);
    } catch (error) {
      receipt.status = 'rejected'; receipt.message = error instanceof BridgeError ? error.message : 'Harness 未接受操作，请重新同步';
      receipt.error_code = error.code || 'HARNESS_ERROR';
    }
    this.refresh();
    const {fingerprint: _, ...result} = receipt;
    return clone(result);
  }
  hold(id) {
    if (this.holds.has(id)) return;
    const item = this.requests.get(id);
    if (!item?.timed || !item.call_id || item.mode !== 'live') return;
    const agent = this.ctx.agents.get(item.session_id);
    if (!agent) return;
    const lifetime = new AbortController();
    this.holds.set(id, lifetime);
    (async () => {
      try { for await (const _ of this.ctx.userQuestions.attachWait(agent, item.call_id, lifetime.signal)) { /* holding while typing */ } }
      catch { /* cancellation is normal */ }
      finally { if (this.holds.get(id) === lifetime) this.holds.delete(id); }
    })();
  }
  releaseHold(id) { this.holds.get(id)?.abort(); this.holds.delete(id); }
  releaseHolds() { for (const id of this.holds.keys()) this.releaseHold(id); }
  async close() {
    this.closed = true; this.connected = false; this.controller.abort(); this.releaseHolds();
    await this.worker;
  }
}
