"""Per-installation private CA and browser-valid LAN leaf certificates.

Only the public CA is exported. Private material is protected by Windows DPAPI.
Nothing installs trust or changes firewall rules automatically.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import ipaddress
import json
import ssl
import tempfile
import uuid
import base64
import os

from ..multi_diverse.identity import protect, atomic


def _private_directory(path):
    """Protect the SSL loader's temporary plaintext before writing any key."""
    if os.name != 'nt':
        os.chmod(path, 0o700)
        return
    import ctypes as ct
    from ctypes import wintypes as wt
    api=ct.windll.advapi32
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[wt.LPCWSTR,wt.DWORD,ct.POINTER(ct.c_void_p),ct.c_void_p]
    api.SetFileSecurityW.argtypes=[wt.LPCWSTR,wt.DWORD,ct.c_void_p]
    ct.windll.kernel32.LocalFree.argtypes=[ct.c_void_p]
    descriptor=ct.c_void_p()
    # Owner Rights and SYSTEM; inherited read permissions are removed before
    # the PEM file exists. The ACL is scoped to this temporary directory only.
    if not api.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)',1,ct.byref(descriptor),None):
        raise ValueError('无法保护临时 TLS 目录，未写入私钥')
    try:
        if not api.SetFileSecurityW(str(path),0x80000004,descriptor):
            raise ValueError('无法保护临时 TLS 目录，未写入私钥')
    finally:ct.windll.kernel32.LocalFree(descriptor)


class ServerIdentity:
    def __init__(self, root, address, *, codec=protect, hostname='', imported=False):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
        root = Path(root) / 'remote' / 'tls'
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'identity.dpapi'
        now = datetime.now(timezone.utc)
        if path.exists():
            value = json.loads(codec(path.read_bytes(), True))
            ca = x509.load_pem_x509_certificate(value['ca'].encode())
            key = serialization.load_pem_private_key(value['key'].encode(), None)
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SOLO local device CA')])
            ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
                  .not_valid_after(now+timedelta(days=3650))
                  .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
                  .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
                  .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
                  .sign(key, hashes.SHA256()))
            value = dict(id=uuid.uuid4().hex, ca=ca.public_bytes(serialization.Encoding.PEM).decode(),
                         key=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()).decode())
            atomic(path, codec(json.dumps(value).encode()))
        if ca.not_valid_after_utc <= now + timedelta(days=1):
            raise ValueError('本机 CA 即将过期，请迁移证书并重新建立信任，未自动更换身份。')
        self.id = value['id']
        self.fingerprint = ca.fingerprint(hashes.SHA256()).hex()
        self.public_path = root / 'SOLO-device-CA.crt'
        atomic(self.public_path, value['ca'].encode())
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SOLO '+self.id[:12])])
        addresses = [x509.IPAddress(ipaddress.ip_address(address))]
        if hostname and hostname != address:
            try: addresses.append(x509.IPAddress(ipaddress.ip_address(hostname)))
            except ValueError: addresses.append(x509.DNSName(hostname))
        san = x509.SubjectAlternativeName(addresses)
        leaf = (x509.CertificateBuilder().subject_name(name).issuer_name(ca.subject).public_key(leaf_key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
                .not_valid_after(min(now+timedelta(days=30), ca.not_valid_after_utc))
                .add_extension(san, False).add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), False)
                .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
                .sign(key, hashes.SHA256()))
        chain = leaf.public_bytes(serialization.Encoding.PEM)+value['ca'].encode()
        if imported:
            configured = root/'external.dpapi'
            if not configured.exists(): raise ValueError('尚未导入受信任的站点证书和私钥')
            external = json.loads(codec(configured.read_bytes(),True))
            leaf = x509.load_pem_x509_certificate(external['cert'].encode())
            leaf_key = serialization.load_pem_private_key(external['key'].encode(),None)
            if not leaf.not_valid_before_utc <= now < leaf.not_valid_after_utc:
                raise ValueError('导入的站点证书未生效或已过期')
            try: names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            except x509.ExtensionNotFound: raise ValueError('站点证书缺少 SAN 名称') from None
            endpoint = hostname or address
            # Deliberately require exact names; wildcard policies need explicit review.
            if endpoint not in [str(v) for v in names.get_values_for_type(x509.IPAddress)]+names.get_values_for_type(x509.DNSName):
                raise ValueError('站点证书 SAN 必须包含实际访问的 IP 或完整主机名')
            chain=external['cert'].encode()
            self.public_path=root/'SOLO-server-chain.crt';atomic(self.public_path,chain)
            self.fingerprint=leaf.fingerprint(hashes.SHA256()).hex()
        self.leaf_spki = base64.b64encode(hashlib.sha256(leaf_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).digest()).decode()
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        # Restrict temporary cleartext material to the existing local app data root.
        with tempfile.TemporaryDirectory(dir=root, prefix='load-') as tmp:
            _private_directory(tmp)
            cert_path, key_path = Path(tmp)/'leaf.pem', Path(tmp)/'key.pem'
            cert_path.write_bytes(chain)
            key_path.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            self.context.load_cert_chain(cert_path, key_path)


def import_certificate(root, certificate, private_key, *, codec=protect):
    """Validate matching PEM material before storing an encrypted copy."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert=Path(certificate).read_bytes();key=Path(private_key).read_bytes()
    if len(cert)>128*1024 or len(key)>32*1024: raise ValueError('证书文件过大')
    leaf=x509.load_pem_x509_certificate(cert)
    loaded=serialization.load_pem_private_key(key,None)
    public=lambda k:k.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
    if public(leaf.public_key())!=public(loaded.public_key()): raise ValueError('证书与私钥不匹配')
    directory=Path(root)/'remote'/'tls';directory.mkdir(parents=True,exist_ok=True)
    atomic(directory/'external.dpapi',codec(json.dumps({'cert':cert.decode(),'key':key.decode()}).encode()))
