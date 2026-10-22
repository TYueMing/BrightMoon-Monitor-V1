"""Owner-only bridge routing; no keys, paths or bridge endpoints cross the LAN."""
from ..deepseek_monitor import bridge_client


def send_harness(run,operation,payload):
    request=next((r for r in run.get('deepseek_interactions',[])
                  if r.get('id')==payload.get('confirmation_id') and r.get('revision')==payload.get('expected_revision')),None)
    if not request:raise ValueError('Harness 请求已更新')
    actions={'approve_tool':'approve','deny_tool':'deny','answer_question':'answer'}
    if operation not in actions:raise ValueError('Harness 不支持此操作')
    if (operation=='answer_question')!=(request.get('kind')=='question'):raise ValueError('请求类型不匹配')
    answer=None
    if operation=='answer_question':
        questions=request.get('questions',[]);answers=payload.get('answers')
        if not isinstance(answers,list) or len(answers)!=len(questions):raise ValueError('回答数量不匹配')
        values=[]
        for question,items in zip(questions,answers):
            if not isinstance(items,list) or not items or any(not isinstance(v,str) for v in items):raise ValueError('回答无效')
            choices={o['label'] for o in question.get('options',[])}
            selected=[v for v in items if v in choices];custom=[v for v in items if v not in choices]
            if len(custom)>1 or (not question.get('multiSelect') and len(selected)>1):raise ValueError('回答格式无效')
            values.append(dict(id=question['id'],selected=selected,**({'custom':custom[0]} if custom else {})))
        answer={'answers':values}
    # The existing BridgeClient rechecks instance, generation, request revision
    # and freshness at submission. No retry after a possibly accepted write.
    return bridge_client.submit(request,actions[operation],answer).result(timeout=3)
