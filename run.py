"""Run from any working directory; all application imports stay in this project."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    # Command hooks must not enter the single-instance desktop/tray lifecycle.
    if "--claude-hook" in sys.argv:
        from monitor.claude_monitor.hook import main
        raise SystemExit(main([arg for arg in sys.argv[1:] if arg != "--claude-hook"]))
    from monitor.runtime_check import ensure_runtime
    if not ensure_runtime():
        raise SystemExit(1)
    if sys.argv[1:] == ["--check-runtime"]:
        print("Monitor Solo runtime dependencies: OK")
        raise SystemExit(0)
    from monitor.app import main
    raise SystemExit(main())
