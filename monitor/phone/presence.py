"""Event-loop-owned proof of browser receipt; independent of task revisions."""
import secrets
import time
from .pairing import PairError

class Presence:
    def __init__(self, clock=time.monotonic):
        self.clock, self.viewers = clock, {}
        self.rates={}

    def limit(self,session,kind):
        now=self.clock();key=(session.id,kind)
        self.rates={k:v for k,v in self.rates.items() if now-v[0]<10}
        bucket=self.rates.setdefault(key,[now,0]);bucket[1]+=1
        if bucket[1]>20:raise PairError('连接确认过于频繁，请稍后重试',429)

    def prune(self, sessions):
        ids = {s.id for s in sessions.values()}
        now = self.clock()
        self.viewers = {k:v for k,v in self.viewers.items()
                        if v['session'] in ids and now-v['used'] < 20}

    def register(self, session, previous=None):
        self.limit(session,'register')
        if previous is not None and (not isinstance(previous,str) or len(previous)>128):raise PairError('查看页标识无效')
        now = self.clock()
        self.viewers = {k:v for k,v in self.viewers.items() if now-v['used'] < 20}
        if previous in self.viewers and self.viewers[previous]['session'] == session.id:
            return previous
        if len(self.viewers) >= 6 or sum(v['session']==session.id for v in self.viewers.values()) >= 2:
            raise PairError('请关闭旧查看页，或等待 20 秒后重试',429)
        key = secrets.token_urlsafe(18)
        self.viewers[key] = dict(session=session.id,used=now,ack=None,pending={})
        return key

    def challenge(self, session, key):
        v = self.viewers.get(key)
        if not v or v['session'] != session.id:
            raise PairError('查看页已失效，请重新连接',409)
        now = self.clock()
        v['used'] = now
        v['pending'] = {k:t for k,t in v['pending'].items() if now-t < 15}
        if len(v['pending']) >= 16:
            v['pending'].pop(next(iter(v['pending'])))
        token = secrets.token_urlsafe(18)
        v['pending'][token] = now
        return dict(viewer_id=key,challenge=token)

    def acknowledge(self, session, key, challenge):
        self.limit(session,'ack')
        if not isinstance(key,str) or not isinstance(challenge,str) or max(len(key),len(challenge))>128:raise PairError('收包确认无效')
        v = self.viewers.get(key)
        if not v or v['session'] != session.id:
            raise PairError('查看页已失效',409)
        issued = v['pending'].pop(challenge,None)
        if issued is None or self.clock()-issued >= 15:
            raise PairError('收包确认已过期',409)
        v['ack'] = v['used'] = self.clock()

    def online(self, session_id):
        return any(v['session']==session_id and v['ack'] is not None
                   and self.clock()-v['ack'] < 15 for v in self.viewers.values())
