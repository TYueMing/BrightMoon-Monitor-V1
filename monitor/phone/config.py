"""Persist preferences, never pairing codes or browser sessions."""


def normalize(value=None, *, strict=False):
    if value is not None and not isinstance(value, dict) and strict:
        raise ValueError("手机访问设置必须为对象")
    value = value if isinstance(value, dict) else {}
    defaults = dict(enabled=False, interface_id="", port=18765, tls_enabled=True, tls_host='', certificate_mode='local',
                    privacy_mode="summary", share_personalization_images=False)
    result = dict(defaults)
    for key, default in defaults.items():
        candidate = value.get(key, default)
        valid = (type(candidate) is bool if type(default) is bool else
                 type(candidate) is int and 1024 <= candidate <= 65535 if key == "port" else
                 candidate in ("summary", "status_only") if key == "privacy_mode" else
                 candidate in ('local','imported') if key=='certificate_mode' else
                 isinstance(candidate, str) and len(candidate) <= 128 and not any(ord(c) < 32 for c in candidate))
        if not valid:
            if strict:
                raise ValueError("手机访问设置无效：" + key)
            candidate = default
        result[key] = candidate
    import re
    if result['tls_host'] and not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,126}[A-Za-z0-9])?',result['tls_host']):
        if strict: raise ValueError('访问主机名只能是 DNS 名称或 IPv4 地址，不包含协议和端口')
        result['tls_host']=''
    result['tls_host']=result['tls_host'].lower()
    if result['tls_host'] and any(not part or len(part)>63 or part.startswith('-') or part.endswith('-') for part in result['tls_host'].split('.')):
        if strict: raise ValueError('访问主机名的每段须为 1–63 字符，且不能以连字符开头或结尾')
        result['tls_host']=''
    return result
