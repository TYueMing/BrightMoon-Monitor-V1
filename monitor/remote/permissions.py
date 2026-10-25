"""Explicit desktop-issued grants. Existing devices migrate to read-only."""
DEFAULTS = dict(view_tasks=False, edit_tasks=False, delete_tasks=False,
                reminder_act=False, agent_approve=False, agent_answer=False, agent_stop=False)


def normalize(value=None):
    value = value if isinstance(value, dict) else {}
    result = {k: value.get(k) is True for k in DEFAULTS}
    if result['edit_tasks'] or result['delete_tasks'] or result['reminder_act']:
        result['view_tasks'] = True
    return result
