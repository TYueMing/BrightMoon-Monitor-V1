"""Loopback-only browser preview using synthetic tasks and ephemeral pairing."""
from pathlib import Path
import sys
import tempfile
import time
import socket
import argparse
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from monitor.phone.service import PhoneService
from monitor.phone.network import Interface


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-file", type=Path, help="Test-only local selector: tasks/five/idle/stale/private")
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="solo-phone-preview-") as root:
        photos = Path(root)/"backgrounds"
        photos.mkdir()
        image = Image.new("RGB", (600, 1000))
        draw = ImageDraw.Draw(image)
        for y in range(1000):
            draw.line((0,y,600,y), fill=(70+y//10, 105+y//12, 155+y//20))
        draw.ellipse((380,110,480,210), fill="#f9dfaf")
        draw.polygon([(0,1000),(0,640),(210,410),(400,720),(600,480),(600,1000)], fill="#314e68")
        image.save(photos/"preview.png")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        interface = Interface("fixture", "Loopback", "127.0.0.1", 32, "fixture")
        service = PhoneService(root, discovery=lambda: [interface])
        service.start({"enabled": True, "interface_id": "fixture", "port": port})
        start = time.time()
        try:
            while service.snapshot_status()["state"] == "starting":
                time.sleep(.1)
            if service.snapshot_status()["state"] != "running":
                raise RuntimeError(service.snapshot_status()["message"])
            service.command("pair")
            time.sleep(.3)
            status = service.snapshot_status()
            print(status["address"], status["code"], flush=True)
            while time.time()-start < 1200:
                try:
                    scenario = options.scenario_file.read_text(encoding="utf-8").strip() if options.scenario_file else "tasks"
                except OSError:
                    scenario = "tasks"
                if scenario == "stale":
                    time.sleep(.4)
                    continue
                task = {"app_id": "codex", "run_id": "fixture", "turn_id": "1", "status": "running",
                        "started_at": start, "task_summary": "整理项目测试", "summary_preview": "正在补充测试并检查执行结果。手机可以查看同一份任务进展，不会触发额外操作。",
                        "steps": [{"name": "读取项目", "status": "completed"}, {"name": "分析测试", "status": "completed"},
                                  {"name": "运行验证", "status": "running"}, {"name": "汇总结果", "status": "queued"}],
                        "tools": [{"name": "pytest", "status": "running", "detail": "正在检查接口和任务生命周期。"}],
                        "codex": {"usage": {"input": 8500, "window": 128000}, "compactions": 1}}
                request = {**task, "app_id": "opencode", "run_id": "fixture-approval", "task_summary": "读取项目配置",
                           "status": "waiting_confirmation", "pending_confirmations": [{"id": "request-fixture", "tool_name": "读取项目文件", "reason": "分析配置用于下一步验证。", "requested_at": start}]}
                tasks = [task, request]
                if scenario == "five":
                    tasks += [{**task, "run_id": f"fixture-{index}", "task_summary": f"测试任务 {index}"} for index in range(3)]
                elif scenario == "idle":
                    tasks = []
                if scenario == "completed":
                    tasks = [{**task, "status": "completed", "ended_at": start+21}]
                service.config["privacy_mode"] = "status_only" if scenario == "private" else "summary"
                service.config["share_personalization_images"] = scenario in ("completed", "wallpaper")
                service.publish({"preferences": {"monitor_background_color": "#f1f5f9", "monitor_opacity": 75, "island_resident": True,
                                                  "background_mode": "photo", "background_photo": "backgrounds/preview.png",
                                                  "monitor_background_mode": "photo", "monitor_background_photo": "backgrounds/preview.png",
                                                  "panel_color": "#ffffff", "panel_transparency": 35},
                                 "upcoming_reminder_count": 2, "next_reminder": {"title": "喝杯水休息", "body": "给眼睛留一点休息时间。", "remind_at": start+3600},
                                 "source_states": {"codex": {"status": "ready"}, "opencode": {"status": "ready"}}},
                                tasks, "fixture", True)
                time.sleep(.4)
        finally:
            service.stop()
            service.thread.join(3)


if __name__ == "__main__":
    main()
