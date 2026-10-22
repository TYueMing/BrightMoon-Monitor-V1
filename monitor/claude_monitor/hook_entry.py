"""Absolute command-hook launcher for source and frozen builds."""
import sys
from pathlib import Path
if __name__ == '__main__':
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root))
    from monitor.claude_monitor.hook import main
    raise SystemExit(main())
