"""Bounded UTF-8 wire projection; do not send raw provider records to hardware."""
from datetime import datetime
import hashlib
import json
import secrets

from ..phone.projector import PhoneProjector, chain as project_chain
from ..UI.common_model import STATUS_TEXT, elapsed


def clip(value, capacity):
    # Capacities include the trailing NUL in the firmware's fixed-size fields.
    return str(value or '').encode('utf-8')[:capacity-1].decode('utf-8', 'ignore')


def frame(value):
    encoded = b'SOLO1 STATE ' + json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(encoded) > 8000: raise ValueError('ESP32 状态超过串口帧上限')
    return encoded


class CompactProjector:
    def __init__(self):
        self.phone = PhoneProjector(secrets.token_bytes(32))
        self.epoch = secrets.token_hex(7)
        self.revision = 0

    def project(self, snapshot, visible, focus, desktop_visible, privacy='summary', handles=None, control_enabled=False):
        handles = handles or {}
        reminders = [r for r in visible if (r.get('remote_source') or r.get('app_id'))=='solo_task']
        agents = [r for r in visible if r not in reminders]
        projected = self.phone.project(snapshot, agents, focus, desktop_visible, {'privacy_mode': privacy})
        raw = {self.phone.key(r): r for r in agents[:5]}
        tasks = []
        colors = {'completed': '#32b786', 'failed': '#e76072', 'cancelled': '#8e98aa',
                  'waiting_confirmation': '#e4a63e', 'waiting_input': '#e4a63e', 'paused': '#e4a63e'}
        for item in projected['tasks']:
            run = raw[item['task_key']]
            remote = item['origin'] == 'remote'
            device = str(run.get('remote_origin') or run.get('remote_name') or 'peer') if remote else 'local'
            chain = item['chain']
            if remote:
                # Peer transport normalizes plans into `steps`; keep observed
                # Claude/DeepSeek events distinct from a measurable plan.
                normalized = {**run, 'app_id': item['app_id']}
                if item['app_id'] == 'opencode': normalized['tasks'] = run.get('steps', [])
                chain = project_chain(normalized, privacy == 'status_only')
            planned = chain['kind'] == 'plan' and chain['completed'] is not None and not chain['truncated']
            step = next((n['label'] for n in chain['nodes'] if n['status'] == 'running'), '')
            attention = item['attention']
            status = item['status']
            failed_link = item['connection_state'] == 'error'
            tasks.append(dict(k=item['task_key'][:23], app=clip(item['app_id'], 20),
                name=clip(item['source'].split(' · ')[-1], 24),
                dev=clip(item['device_name'] if remote else '本机', 24),
                device=hashlib.sha256(device.encode()).hexdigest()[:16] if remote else 'local',
                glyph={'codex': 'C', 'claude': 'A', 'opencode': 'O', 'solo_task': 'T'}.get(item['app_id'], 'B'),
                status=clip(status, 28), label=clip('连接中断' if failed_link else STATUS_TEXT.get(status, '未知'), 32),
                color='#e76072' if failed_link else colors.get(status, '#598deb'),
                title=clip(item['title'], 96), sum=clip(item['summary'], 220), err=failed_link,
                final=item['timer']['final'], remote=remote, elapsed=min(2147483647, sum(int(v)*m for v, m in zip(elapsed(run).split(':'), (60, 1)))),
                plan={'done': chain['completed'] if planned else -1, 'total': chain['total'] if planned else 0,
                      'label': clip(chain['label'], 32), 'step': clip(step, 96)},
                att={'title': clip(attention['title'], 32), 'reason': clip(attention['reason'], 220)} if attention else False,
                actions=[],control=handles.get(item['task_key'][:23],'')))
        appearance = projected['appearance']
        bg = appearance['background_color']
        light = sum(int(bg[i:i+2], 16)*w for i, w in ((1, .2126), (3, .7152), (5, .0722))) > 150
        now = datetime.now()
        idle = projected['idle']; reminder = idle['next_reminder'] or {}
        at = reminder.get('remind_at_ms')
        rems=[]
        for run in reminders[:5]:
            r=self.phone.task(run, privacy=='status_only',len(rems)); remote=bool(run.get('remote_origin'))
            due=(run.get('remote_entity',{}).get('reminder',{}).get('due_at') if remote else None)
            due=due or (r['reminder_due_at_ms']/1000 if r.get('reminder_due_at_ms') else None)
            rems.append(dict(k=r['task_key'][:23],title=clip(r['title'],96),sum=clip(r['summary'],220),
                dev=clip(run.get('remote_name') or '本机',24),control=handles.get(r['task_key'][:23],''),
                at=datetime.fromtimestamp(due).strftime('%H:%M') if due else '',active=True))
        devices=[dict(id='local',name='本机',online=True)]
        for peer in snapshot.get('esp32_peers',[])[:5]:
            devices.append(dict(id=hashlib.sha256(str(peer['id']).encode()).hexdigest()[:16],name=clip(peer.get('name'),24),online=bool(peer.get('online'))))
        for item in tasks:
            if not any(d['id']==item['device'] for d in devices) and len(devices)<6:
                devices.append(dict(id=item['device'],name=item['dev'],online=not item['err']))
        self.revision = (self.revision + 1) & 0xffffffff
        value = dict(v=1, rev=self.revision, epoch=self.epoch, now=now.strftime('%H:%M'), date=now.strftime('%m-%d'),
            desktop=desktop_visible, attention=any(t['att'] for t in tasks), privacy=privacy,
            control_mode='privacy' if privacy=='status_only' else 'enabled' if control_enabled else 'disabled',
            mode='active' if tasks else 'idle', focus=projected['desktop_focus_key'][:23], tasks=tasks,
            devices=devices,reminders=rems,task_total=len(agents),reminder_total=len(reminders),
            theme=dict(bg=bg, fg='#20324b' if light else '#eff4ff', muted='#63738b' if light else '#a5b5cc',
                       panel=appearance['panel_color'], panel_a=round(appearance['panel_alpha']*100)),
            idle=dict(preview=idle['preview_enabled'], count=idle['upcoming_count'], title=clip(reminder.get('title'), 96),
                      at=datetime.fromtimestamp(at/1000).strftime('%m-%d %H:%M') if at else '',body=clip(reminder.get('body'),220)))
        if len(json.dumps(value,ensure_ascii=False).encode())>7700:
            for item in tasks+rems: item['sum']=clip(item['sum'],80)
        frame(value)  # Validate before replacing a last-known-good publication.
        return value
