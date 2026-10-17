"""Run from any working directory; all application imports stay in this project."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    from monitor.app import main
    raise SystemExit(main())
