"""Atomic, bounded trusted-device registry. Raw browser credentials never hit disk."""
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4


class TrustedDevices:
    LIMIT = 3

    def __init__(self, data_root):
        self.path = Path(data_root) / "phone" / "devices.json"
        self.lock = RLock()

    def read(self):
        with self.lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {}
            except (OSError, ValueError) as error:
                raise ValueError("长期设备记录无法读取，请检查 data/phone/devices.json。") from error
            if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("devices"), list) or len(raw["devices"]) > self.LIMIT:
                raise ValueError("长期设备记录格式错误；未覆盖原文件。")
            result = {}
            ids = set()
            for row in raw["devices"]:
                if (not isinstance(row, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("digest", "")))
                    or not re.fullmatch(r"[a-f0-9]{24}", str(row.get("id", "")))
                    or not isinstance(row.get("name"), str) or len(row["name"]) > 32
                    or row["digest"] in result or row["id"] in ids):
                    raise ValueError("长期设备记录格式错误；未覆盖原文件。")
                result[row["digest"]] = {k: row[k] for k in ("id", "name", "digest")}
                ids.add(row["id"])
            return result

    def _write(self, records):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"devices-{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps({"schema": 1, "devices": list(records.values())}, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def remember(self, digest, device_id, name):
        with self.lock:
            records = self.read()
            if digest not in records and len(records) >= self.LIMIT:
                raise ValueError("最多长期保存 3 台设备，请先删除旧设备。")
            records[digest] = {"digest": digest, "id": device_id, "name": name}
            self._write(records)

    def remove(self, device_id):
        with self.lock:
            records = self.read()
            remaining = {k: v for k, v in records.items() if v["id"] != device_id}
            if remaining != records:
                self._write(remaining)
