"""Calendar repeats and elapsed-hour repeats anchored to the first due time."""
from datetime import date, datetime, time, timedelta


def validate(value):
    if not isinstance(value.get("title"),str) or not isinstance(value.get("body",""),str):
        raise ValueError("标题和内容须为文本")
    title = value["title"].strip()
    body = value.get("body", "").strip()
    if not title or len(title) > 40 or any(c in title for c in "\r\n\t"):
        raise ValueError("标题须为 1–40 个字符，不能换行")
    if len(body) > 4000:
        raise ValueError("内容最多 4000 个字符")
    try:
        start = date.fromisoformat(str(value.get("start_date", "")))
        clock = datetime.strptime(str(value.get("time_of_day", "")), "%H:%M").time()
    except ValueError:
        raise ValueError("请填写有效日期 YYYY-MM-DD 和时间 HH:MM") from None
    weekdays = value.get("weekdays", [])
    if not isinstance(weekdays, list) or any(type(i) is not int or not 0 <= i <= 6 for i in weekdays):
        raise ValueError("重复星期须为星期一至星期日")
    try:
        raw_interval = value.get("interval_hours", 0)
        interval = int(raw_interval)
        if isinstance(raw_interval, bool) or float(raw_interval) != interval or not 0 <= interval <= 8760:
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("间隔须为 1–8760 小时的整数，0 表示不启用") from None
    if interval and weekdays:
        raise ValueError("每周重复与间隔重复不能同时选择")
    try:
        raw = value.get("lead_minutes", 5)
        lead = int(raw)
        if isinstance(raw, bool) or float(raw) != lead or not 2 <= lead <= 10:
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("提前提醒须为 2–10 分钟的整数") from None
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("启用状态无效")
    return dict(title=title, body=body, start_date=start.isoformat(),
                time_of_day=clock.strftime("%H:%M"), weekdays=sorted(set(weekdays)),
                lead_minutes=lead, enabled=enabled, interval_hours=interval)


def due_on(task, day):
    start = date.fromisoformat(task["start_date"])
    if day < start or (task["weekdays"] and day.weekday() not in task["weekdays"]):
        return None
    if not task["weekdays"] and day != start:
        return None
    wall = datetime.combine(day, time.fromisoformat(task["time_of_day"]))
    # Skip nonexistent spring-forward wall times. Ambiguous fall-back times use
    # fold=0; the occurrence's calendar-date key prevents a second notification.
    try:
        stamp = wall.timestamp()
        return stamp if datetime.fromtimestamp(stamp) == wall else None
    except (OverflowError, OSError, ValueError):
        return None  # The platform cannot represent this calendar date.


def next_due(task, now):
    if not task["enabled"]:
        return None
    interval = task.get("interval_hours", 0)
    if interval:
        anchor = due_on(task, date.fromisoformat(task["start_date"]))
        if anchor is None:
            return None
        seconds = interval * 3600
        # Direct arithmetic skips missed cycles after sleep/restart. Snoozing
        # never moves this anchor, so a two-hour schedule does not drift.
        cycle = max(0, int((now-anchor)//seconds)+1)
        due = anchor + cycle*seconds
        try:
            datetime.fromtimestamp(due)
        except (OverflowError, OSError, ValueError):
            return None
        return due
    first = max(datetime.fromtimestamp(now).date(), date.fromisoformat(task["start_date"]))
    for offset in range(8 if task["weekdays"] else 1):
        try:
            day = first + timedelta(days=offset)
        except OverflowError:
            break
        due = due_on(task, day)
        if due is not None and due > now:
            return due
    return None


def repeat_label(task):
    if task.get("interval_hours"):
        return f'每隔 {task["interval_hours"]} 小时'
    return "每周 " + "、".join("一二三四五六日"[i] for i in task["weekdays"]) if task["weekdays"] else "仅一次"


def occurrence_key(task, due):
    if task.get("interval_hours"):
        anchor = due_on(task, date.fromisoformat(task["start_date"]))
        return f'interval:{round((due-anchor)/(task["interval_hours"]*3600))}'
    return datetime.fromtimestamp(due).date().isoformat()


def stamp_label(value):
    return datetime.fromtimestamp(value).strftime("%m-%d %H:%M") if value is not None else "无后续安排"
