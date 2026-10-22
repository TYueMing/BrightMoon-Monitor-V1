"""Reuse trusted source size policies without giving imported data a renderer."""
from ...UI.detail_panels import panel_for


class SizeContext:
    def __init__(self,ui):
        self.ui=ui
        remote=ui._run();entity=remote['remote_entity']
        self.run={**remote,**entity.get('layout',{}),'app_id':remote['remote_source']}
        self._completion_rows={remote['run_id']:ui.details}
        approval=entity.get('approval')
        if approval:
            questions=[dict(question=q['question'],options=[{'label':o} for o in q['options']],multiple=q['multiple'],custom=q.get('custom',True)) for q in entity.get('questions',[])]
            self.run['pending_confirmations']=[dict(id=approval['id'],status='pending',kind='question' if questions else 'approval',questions=questions)]
            if remote['remote_source']=='deepseek_harness':self.run['deepseek_interactions']=[{}]
    def __getattr__(self,name):return getattr(self.ui,name)
    def _run(self):return self.run
    def _state(self):return self.run['status']


def detail_size(ui):
    source=ui._run().get('remote_source')
    if source not in ('codex','opencode','deepseek_harness','claude','solo_task'):return 420,580 if ui.details else 244
    context=SizeContext(ui)
    return panel_for(source).size(context)
