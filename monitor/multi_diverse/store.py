"""Network-thread SQLite ownership; atomic stream/cache and command receipts."""
import json
import sqlite3
import time
from .identity import protect

class Store:
    def __init__(self,path,*,codec=protect):
        self.codec=codec
        self.db=sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=3000')
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS peers(id TEXT PRIMARY KEY,value BLOB NOT NULL);
        CREATE TABLE IF NOT EXISTS cache(id TEXT PRIMARY KEY,epoch TEXT,seq INTEGER,value TEXT);
        CREATE TABLE IF NOT EXISTS journal(peer TEXT,seq INTEGER,value TEXT,at REAL,PRIMARY KEY(peer,seq));
        CREATE TABLE IF NOT EXISTS streams(peer TEXT PRIMARY KEY,seq INTEGER,value TEXT);
        CREATE TABLE IF NOT EXISTS commands(peer TEXT,id TEXT,hash TEXT,result TEXT,at REAL,PRIMARY KEY(peer,id));
        """)
    def peers(self):
        return {k:json.loads(self.codec(v,True)) for k,v in self.db.execute('SELECT id,value FROM peers')}
    def save_peer(self,p):
        if p['id'] not in self.peers() and len(self.peers())>=5:raise ValueError('最多配对 5 台电脑')
        previous=self.peers().get(p['id'],{})
        changed=previous and (previous.get('grants')!=p.get('grants') or previous.get('paused',False)!=p.get('paused',False))
        p={**p,'policy_revision':previous.get('policy_revision',1)+(1 if changed else 0)}
        with self.db:self.db.execute('INSERT OR REPLACE INTO peers VALUES (?,?)',(p['id'],self.codec(json.dumps(p).encode())))
    def remove_peer(self,id):
        with self.db:
            for table,col in [('peers','id'),('cache','id'),('journal','peer'),('streams','peer'),('commands','peer')]:
                self.db.execute(f'DELETE FROM {table} WHERE {col}=?',(id,))
    def clear_projection(self,peer):
        # Do not replay content granted by a previous, more permissive policy.
        # Keep the sequence watermark, replace its body and force a new revision.
        with self.db:
            self.db.execute('DELETE FROM journal WHERE peer=?',(peer,))
            self.db.execute("UPDATE streams SET value='[]',seq=seq+1 WHERE peer=?",(peer,))
    def publish(self,peer,view):
        body=json.dumps(view,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        row=self.db.execute('SELECT seq,value FROM streams WHERE peer=?',(peer,)).fetchone()
        if row:
            previous=json.loads(row[1])
            if [{k:v for k,v in r.items() if k!='elapsed_ms'} for r in previous]==[{k:v for k,v in r.items() if k!='elapsed_ms'} for r in view]:return row[0],False
        seq=(row[0] if row else 0)+1
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO streams VALUES (?,?,?)',(peer,seq,body))
            self.db.execute('INSERT INTO journal VALUES (?,?,?,?)',(peer,seq,body,time.time()))
            # Each journal event is a full bounded subscription replacement.
            # Keep a bounded recovery tail; clients can always request a baseline.
            self.db.execute('DELETE FROM journal WHERE peer=? AND (seq<? OR at<?)',(peer,seq-32,time.time()-86400))
        return seq,True
    def receive(self,peer,epoch,seq,value):
        row=self.db.execute('SELECT epoch,seq,value FROM cache WHERE id=?',(peer,)).fetchone()
        if row and ':' in row[0] and ':' in epoch:
            prior_epoch,prior_policy=row[0].rsplit(':',1);current_epoch,current_policy=epoch.rsplit(':',1)
            if prior_epoch==current_epoch and int(current_policy)<int(prior_policy):raise ValueError('共享权限版本倒退')
        if row and row[0]==epoch and seq<row[1]:raise ValueError('共享目录序号倒退，需要重新确认设备')
        if row and row[0]==epoch:
            from .protocol import FINAL
            previous={v['id']:v for v in json.loads(row[2])}
            for item in value:
                old=previous.get(item['id'])
                if old and old.get('status') in FINAL and item.get('status') not in FINAL:raise ValueError('终态不能倒退')
            if seq==row[1]:
                normalize=lambda rows:[{k:v for k,v in r.items() if k!='elapsed_ms'} for r in rows]
                if normalize(json.loads(row[2]))!=normalize(value):raise ValueError('相同序号内容不一致')
        with self.db:self.db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?)',(peer,epoch,seq,json.dumps(value,ensure_ascii=False)))
    def command_result(self,peer,id,digest):
        row=self.db.execute('SELECT hash,result FROM commands WHERE peer=? AND id=?',(peer,id)).fetchone()
        if row:
            if row[0]!=digest:raise ValueError('相同命令 ID 的内容发生变化')
            return json.loads(row[1])
        return None
    def begin_command(self,peer,id,digest):
        existing=self.command_result(peer,id,digest)
        if existing is not None:return existing
        with self.db:self.db.execute('DELETE FROM commands WHERE at<?',(time.time()-7*86400,))
        if self.db.execute('SELECT COUNT(*) FROM commands').fetchone()[0]>=50000:raise ValueError('命令收据达到容量上限')
        result=dict(status='outcome_unknown',message='操作结果尚待来源核对')
        with self.db:self.db.execute('INSERT INTO commands VALUES (?,?,?,?,?)',(peer,id,digest,json.dumps(result),time.time()))
        return None
    def finish_command(self,peer,id,result):
        with self.db:self.db.execute('UPDATE commands SET result=? WHERE peer=? AND id=?',(json.dumps(result),peer,id))
    def close(self):
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)');self.db.close()
