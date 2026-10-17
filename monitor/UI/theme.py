"""Shared status colors and color interpolation."""

COLORS = {
    "reminding": "#ffb638",
    "running": "#477cff", "waiting_model": "#628cff", "waiting_confirmation": "#ffb638",
    "waiting_lock": "#ab8bf2", "queued": "#8895ae", "waiting": "#8895ae",
    "cancellation_requested": "#ffb638", "completed": "#3ed6a1",
    "failed": "#ff6c7c", "stale": "#ff6c7c", "error": "#ff6c7c",
    "cancelled": "#9ba9bd", "offline": "#8190a9", "pending": "#66748b",
    "partial": "#ffb638", "blocked": "#ffb638", "budget_exhausted": "#ffb638",
    "waiting_input": "#ffb638", "paused": "#ab8bf2", "interrupted": "#9ba9bd",
    "denied": "#ff6c7c", "unknown": "#ffb638", "expired": "#9ba9bd", "superseded": "#9ba9bd",
}
ACTIVE = {"running", "waiting_model", "waiting_confirmation", "waiting_lock", "queued", "cancellation_requested"}
def blend(a: str, b: str, amount: float) -> str:
    return "#" + "".join(
        f"{round(int(a[i:i+2], 16) * (1-amount) + int(b[i:i+2], 16) * amount):02x}"
        for i in (1, 3, 5)
    )

