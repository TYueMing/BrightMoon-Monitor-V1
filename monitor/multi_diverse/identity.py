"""Stable identity and standard TLS keys protected with Windows user DPAPI."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import ssl
import tempfile
import uuid
from datetime import datetime,timedelta,timezone

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def protect(data, decrypt=False):
    if os.name!='nt':
        raise RuntimeError('电脑互信密钥目前需要 Windows DPAPI')
    buf=ctypes.create_string_buffer(data)
    src=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));dst=Blob()
    api=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(src),None,None,None,None,1,ctypes.byref(dst)):
        raise OSError('设备密钥无法解锁；请勿复制其他 Windows 用户的数据目录')
    try:return ctypes.string_at(dst.data,dst.size)
    finally:ctypes.windll.kernel32.LocalFree(dst.data)

def atomic(path,data):
    tmp=path.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    try:
        tmp.write_bytes(data);os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)

class Identity:
    def __init__(self,root,*,codec=protect):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import serialization,hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.codec=codec
        path=self.root/'identity.dpapi'
        if path.exists():
            value=json.loads(codec(path.read_bytes(),True))
        else:
            key=ec.generate_private_key(ec.SECP256R1());device=uuid.uuid4().hex
            subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,device)])
            now=datetime.now(timezone.utc)
            cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                  .public_key(key.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=3650))
                  .sign(key,hashes.SHA256()))
            value=dict(id=device,epoch=uuid.uuid4().hex,
                key=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),
                cert=cert.public_bytes(serialization.Encoding.PEM).decode())
            atomic(path,codec(json.dumps(value).encode()))
        self.id,self.epoch=value['id'],value['epoch']
        self.fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert(value['cert'])).hexdigest()
        self.context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version=ssl.TLSVersion.TLSv1_2
        # Private key exists only while SSL loads it, under this user's data root.
        with tempfile.TemporaryDirectory(dir=self.root,prefix='tls-') as temp:
            certpath=Path(temp)/'cert.pem';keypath=Path(temp)/'key.pem'
            certpath.write_text(value['cert']);keypath.write_text(value['key']);os.chmod(keypath,0o600)
            self.context.load_cert_chain(certpath,keypath)
