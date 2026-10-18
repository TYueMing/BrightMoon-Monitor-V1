from pathlib import Path
import sys
import gc
import pytest

# Explicit project root prevents an enclosing checkout from providing imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def collect_ui_cycles_on_test_thread():
    """Destroy unreachable Tk callback cycles before another network test runs.

    Otherwise GC in an aiohttp worker can finalize a prior test's Tcl interpreter
    on the wrong thread, causing sporadic Tcl errors and stalled worker startup.
    """
    gc.collect()
    yield
    gc.collect()
