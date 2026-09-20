"""Generate 200 traces from the LangGraph request-triage agent.

Uses trace_graph() to wrap the compiled LangGraph and export traces via OTLP.
"""
import os, sys, random, time

os.environ.setdefault('OTEL_SERVICE_NAME', 'm13-langgraph-agent')
os.environ.setdefault('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4317')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'examples', 'demo-agent', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'packages', 'python-sdk', 'src'))

from agent_exec_trace.config import SDKConfig
from agent_exec_trace.langgraph import trace_graph
from agent_exec_trace.tracer import configure_otlp_tracing

configure_otlp_tracing(SDKConfig(
    service_name='m13-langgraph-agent',
    otlp_endpoint='http://localhost:4317',
))

from request_triage.graph import DEFAULT_VERSION, build_graph
from request_triage.seeds import all_requests

graph = build_graph()
traced = trace_graph(
    graph,
    agent_name='request-triage',
    agent_version=DEFAULT_VERSION,
)

requests = all_requests()
scenarios = list(requests.values())
print(f'Generating 200 traces from LangGraph request-triage agent ({len(scenarios)} scenarios)...')

for i in range(100):
    seed = random.choice(scenarios)
    result = traced.invoke(seed)
    if (i + 1) % 50 == 0:
        outcome = result.get('outcome', 'unknown') if isinstance(result, dict) else 'done'
        steps = len(result.get('tool_log', [])) if isinstance(result, dict) else 0
        print(f'  [{i+1:>4}] {str(seed.get("intent",""))[:30]:<30} → {outcome} ({steps} steps)')

print(f'Done: 100 traces generated')
