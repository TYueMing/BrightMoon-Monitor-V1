"""Persist preferences, never pairing codes or browser sessions."""


def normalize(value=None, *, strict=False):
    if value is not None and not isinstance(value, dict) and strict:
        raise ValueError("手机访问设置必须为对象")
    value = value if isinstance(value, dict) else {}
    defaults = dict(enabled=False, interface_id="", port=18765,
                    privacy_mode="summary", share_personalization_images=False)
    result = dict(defaults)
    for key, default in defaults.items():
        candidate = value.get(key, default)
        valid = (type(candidate) is bool if type(default) is bool else
                 type(candidate) is int and 1024 <= candidate <= 65535 if key == "port" else
                 candidate in ("summary", "status_only") if key == "privacy_mode" else
                 isinstance(candidate, str) and len(candidate) <= 128 and not any(ord(c) < 32 for c in candidate))
        if not valid:
            if strict:
                raise ValueError("手机访问设置无效：" + key)
            candidate = default
        result[key] = candidate
    return result
