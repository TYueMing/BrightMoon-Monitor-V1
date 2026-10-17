from pathlib import Path
import sys

# Explicit project root prevents an enclosing checkout from providing imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
