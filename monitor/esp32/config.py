"""Persist only user choices, never an open serial handle."""


def normalize(value=None, *, strict=False):
    value = value if isinstance(value, dict) else {}
    result = {"enabled": value.get("enabled", False), "port": value.get("port", ""),
              "serial_number": value.get("serial_number", ""),
              "privacy_mode": value.get("privacy_mode", "summary"),
              "allow_control": value.get("allow_control", False),
              "wifi_enabled":value.get('wifi_enabled',False),"wifi_interface":value.get('wifi_interface','')}
    valid = (isinstance(result['enabled'], bool) and isinstance(result['allow_control'], bool) and isinstance(result['wifi_enabled'],bool)
             and all(isinstance(result[k], str) and len(result[k]) <= 200
                     and not any(ord(c) < 32 for c in result[k]) for k in ('port', 'serial_number','wifi_interface'))
             and result['privacy_mode'] in ('summary', 'status_only'))
    if not valid:
        if strict: raise ValueError('ESP32 连接设置无效')
        return normalize()
    return result
