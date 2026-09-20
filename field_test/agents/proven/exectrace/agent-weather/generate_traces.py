"""Generate traces from PydanticAI v1 weather agent, instrumented with @trace_agent.

The v1 weather agent uses the old PydanticAI API (OPENAI_MODEL provider, name= param).
Per the v1 compatibility shim (agent_exec_trace.pydantic), v1 agents should be
instrumented with @trace_agent + tool_span() instead of a PydanticAI-specific adapter.

This script wraps the agent's run logic in @trace_agent and uses mock tools
so it doesn't need external API keys.
"""
import os, sys, json, asyncio, random

os.environ.setdefault('OPENAI_API_KEY', 'omlx-test')
os.environ.setdefault('OPENAI_BASE_URL', 'http://127.0.0.1:8000/v1')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'packages', 'python-sdk', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from opentelemetry import trace as otel_trace
from agent_exec_trace.config import SDKConfig
from agent_exec_trace.tracer import configure_otlp_tracing
from agent_exec_trace.raw import trace_agent
from agent_exec_trace.spans import tool_span, plan_span
from agent_exec_trace.instrument import set_output

configure_otlp_tracing(SDKConfig(
    service_name='m13-pydantic-v1-agent',
    otlp_endpoint='http://localhost:4317',
))

LOCATIONS = ['london', 'paris', 'tokyo', 'nyc', 'sf', 'berlin', 'sydney', 'mumbai', 'rome', 'cairo']


def mock_get_weather(location: str) -> str:
    """Mock weather tool — no external API needed."""
    temps = {'london': 15, 'paris': 18, 'tokyo': 22, 'nyc': 20, 'sf': 19,
             'berlin': 12, 'sydney': 25, 'mumbai': 30, 'rome': 24, 'cairo': 35}
    conditions = {'london': 'rainy', 'paris': 'cloudy', 'tokyo': 'sunny', 'nyc': 'windy',
                  'sf': 'foggy', 'berlin': 'overcast', 'sydney': 'clear', 'mumbai': 'humid',
                  'rome': 'sunny', 'cairo': 'hot'}
    loc = location.lower()
    temp = temps.get(loc, 20)
    cond = conditions.get(loc, 'unknown')
    return f'Weather in {loc}: {temp}°C, {cond}'


@trace_agent('pydantic-v1-weather', agent_version='v1.0.0', model='Qwen2.5-1.5B-4bit', provider='openai')
def run_weather_agent(location: str) -> str:
    """Run the weather agent with @trace_agent instrumentation.

    This is the v1 compatibility pattern: wrap the agent function with
    @trace_agent and use tool_span() for each tool call, instead of a
    PydanticAI-specific adapter.
    """
    with plan_span(f'Planning weather lookup for {location}'):
        pass

    with tool_span('get_weather', tool_input=json.dumps({'location': location})):
        result = mock_get_weather(location)

    span = otel_trace.get_current_span()
    set_output(span, result)
    return result


async def main():
    print('Generating 200 traces from PydanticAI v1 weather agent...')
    for i in range(100):
        loc = random.choice(LOCATIONS)
        result = run_weather_agent(loc)
        if (i + 1) % 50 == 0:
            print(f'  [{i+1:>4}] {loc:>10} → {result[:50]}')

    print(f'Done: 100 traces generated')


if __name__ == '__main__':
    asyncio.run(main())
