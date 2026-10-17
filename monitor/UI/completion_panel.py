"""Bounded text layout for the completed task panel, in logical pixels."""


def summary_lines(value, measure, width=368):
    text = " ".join(str(value or "").split())[:200]
    if not text:
        text = "本次任务未提供摘要。"
    lines, line = [], ""
    for char in text:
        if line and measure(line + char) > width:
            lines.append(line)
            line = ""
        line += char
    if line:
        lines.append(line)
    return lines
