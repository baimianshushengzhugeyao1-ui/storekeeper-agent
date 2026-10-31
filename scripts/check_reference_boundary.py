"""Probe real-model routing when a customer writes an order number without #."""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from langsmith import tracing_context
from storekeeper.live_model import create_live_model, PROJECT_ROOT
from storekeeper.live_preview import create_live_preview

rows = []
model = create_live_model()
for index, text in enumerate(['帮我取消1036号订单。', 'Please cancel order 1036.']):
    with tracing_context(enabled=False):
        app = create_live_preview(PROJECT_ROOT / 'var' / 'reference_boundary' / str(index), model=model)
        with TestClient(app) as client:
            result = client.post('/api/tickets', json={'ticket_text': text}).json()
            rows.append({'text': text, 'tasks': result.get('tasks'), 'status': result.get('status'),
                         'results': result.get('task_results'),
                         'pending_approvals': len(result.get('pending_approvals', [])),
                         'pending_clarifications': len(result.get('pending_clarifications', []))})
path = PROJECT_ROOT / 'var' / 'reference_boundary' / 'report.json'
path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(rows, ensure_ascii=True))
