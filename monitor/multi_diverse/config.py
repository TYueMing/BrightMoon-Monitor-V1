"""No implicit network exposure: opt-in, physical private interface only."""
import socket

def normalize(raw=None, *, strict=False):
    raw = raw if isinstance(raw,dict) else {}
    result = dict(enabled=False,show_remote=True,interface_id='',port=18766,name=socket.gethostname()[:40])
    for key in result:
        v=raw.get(key,result[key])
        valid = (type(v) is bool if key in ('enabled','show_remote') else type(v) is int and 1024<=v<=65535
                 if key=='port' else isinstance(v,str) and len(v)<=80 and not any(ord(c)<32 for c in v))
        if key=='name' and isinstance(v,str):valid=valid and bool(v.strip())
        if not valid:
            if strict: raise ValueError('电脑协同设置无效')
        else: result[key]=v
    return result

GRANTS = dict(view_status=True,view_details=False,reminder_act=False,agent_approve=False,
              agent_answer=False,agent_stop=False,reshare_to_phone=False,anonymous_titles=False)

def grants(raw=None):
    raw=raw if isinstance(raw,dict) else {}
    value={k:raw.get(k,v) is True for k,v in GRANTS.items()}
    if any(value[k] for k in ('agent_approve','agent_answer','agent_stop')):value['view_status']=value['view_details']=True
    sources=raw.get('sources')
    value['sources']=None if sources is None else [s for s in ('codex','deepseek_harness','opencode','claude','solo_task') if s in sources] if isinstance(sources,list) else []
    return value
