"""Batch trace generator — 200 traces from the raw Python agent, instrumented with SDK."""
import os, sys, json, random, time

os.environ.setdefault('OTEL_SERVICE_NAME', 'm13-raw-agent')
os.environ.setdefault('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4317')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'packages', 'python-sdk', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../agent-raw'))

from opentelemetry import trace as otel_trace
from agent_exec_trace.raw import trace_agent
from agent_exec_trace.spans import tool_span, plan_span
from agent_exec_trace.instrument import set_output
from agent_exec_trace.config import SDKConfig
from agent_exec_trace.tracer import configure_otlp_tracing
from agent import search_kb, lookup_account, escalate

configure_otlp_tracing(SDKConfig(
    service_name='m13-raw-agent',
    otlp_endpoint='http://localhost:4317',
))

QUERIES = [
    ('reset password', 'ACC-001'), ('billing question', 'ACC-999'),
    ('unknown topic', 'ACC-001'), ('downtime status', 'ACC-500'),
    ('password reset urgent', 'ACC-010'), ('billing dispute', 'ACC-750'),
    ('account lockout', 'ACC-001'), ('refund request', 'ACC-300'),
    ('service outage', 'ACC-001'), ('subscription cancel', 'ACC-888'),
]


@trace_agent('raw-support-triage', agent_version='v1.0', workload_type='support')
def instrumented_run(query: str, account_id: str) -> str:
    with plan_span('identify_intent'):
        pass

    with tool_span('search_kb', tool_input=json.dumps({'query': query})):
        kb_result = search_kb(query)

    with tool_span('lookup_account', tool_input=json.dumps({'account_id': account_id})):
        acct_result = lookup_account(account_id)

    if kb_result['status'] == 'ok':
        output = f"Resolved: {kb_result['answer']} (account tier: {acct_result.get('tier', 'unknown')})"
    else:
        with tool_span('escalate', tool_input=json.dumps({'reason': f'No KB answer for: {query}'})):
            esc_result = escalate(f'No KB answer for: {query}')
        output = f"Escalated: ticket {esc_result['ticket_id']} ({esc_result['reason']})"

    span = otel_trace.get_current_span()
    set_output(span, output)
    return output


for i in range(100):
    q, aid = random.choice(QUERIES)
    if i % 7 == 0:
        aid = f'BAD-{random.randint(1,99)}'
    if i % 13 == 0:
        q = 'this query makes no sense ' * 3
    try:
        result = instrumented_run(q, aid)
        if i % 50 == 0:
            print(f'[{i+1:5d}] {q[:30]:30s} → {result[:50]}', flush=True)
    except Exception as e:
        print(f'[{i+1:5d}] ERROR: {e}', flush=True)
    time.sleep(0.001)

print('Done: 100 traces generated')
time.sleep(2)
