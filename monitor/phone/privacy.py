"""Bounded text projection; redaction is defense in depth, not a secrecy guarantee."""
import re


def text(value, limit=200):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    value = str(value)[:12000]
    value = re.sub(r"(?i)(?:bearer\s+)[\w.\-]+", "Bearer [隐藏]", value)
    value = re.sub(r"\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]{20,}|github_pat_[\w]{20,})", "[凭据已隐藏]", value)
    # Include JSON keys and quoted values containing spaces, not just key=value.
    value = re.sub(r'''(?i)((?:password|passwd|api[_-]?key|token|secret|authorization|cookie)["']?\s*[=:]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}]+)''', r"\1[隐藏]", value)
    value = re.sub(r"https?://\S+", "[链接已隐藏]", value)
    value = re.sub(r"[A-Za-z]:[\\/][^\r\n<>\"|]*", "[本地路径已隐藏]", value)
    value = re.sub(r"(?:\\\\|(?<!\w)/(?:home|Users|etc|tmp|var|mnt)/)[^\s\"<>]+", "[本地路径已隐藏]", value)
    value = "".join(c for c in value if ord(c) >= 32 or c in "\n\t")
    return value[:limit] + ("…" if len(value) > limit else "")


def number(value):
    import math
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
