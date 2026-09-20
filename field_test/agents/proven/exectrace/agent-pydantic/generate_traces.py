"""Generate traces from PydanticAI v2 weather agent, instrumented with SDK."""
import os, sys, json, asyncio, time, random

os.environ.setdefault('OPENAI_API_KEY', 'omlx-test')
os.environ.setdefault('OPENAI_BASE_URL', 'http://127.0.0.1:8000/v1')
os.environ['OPENAI_API_KEY'] = 'omlx-test'
os.environ['OPENAI_BASE_URL'] = 'http://127.0.0.1:8000/v1'

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'packages', 'python-sdk', 'src'))

from opentelemetry import trace as otel_trace
from agent_exec_trace.config import SDKConfig
from agent_exec_trace.tracer import configure_otlp_tracing
from agent_exec_trace.raw import trace_agent
from agent_exec_trace.spans import tool_span, plan_span
from agent_exec_trace.instrument import set_output

configure_otlp_tracing(SDKConfig(
    service_name='m13-pydantic-agent',
    otlp_endpoint='http://localhost:4317',
))

from pydantic_ai import Agent, RunContext

weather_agent = Agent(
    'openai:Qwen2.5-1.5B-4bit',
    system_prompt='You are a weather assistant. Answer in one short sentence. Do not think or reason.',
    deps_type=str,
    model_settings={'max_tokens': 4096, 'temperature': 0.1, 'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}},
)


@weather_agent.tool
async def get_weather(ctx: RunContext[str], location: str) -> str:
    temps = {'london': 15, 'paris': 18, 'tokyo': 22, 'nyc': 20, 'sf': 19, 'berlin': 12, 'sydney': 25, 'mumbai': 30}
    conditions = {'london': 'rainy', 'paris': 'cloudy', 'tokyo': 'sunny', 'nyc': 'windy', 'sf': 'foggy', 'berlin': 'rainy', 'sydney': 'sunny', 'mumbai': 'hot'}
    loc = location.lower()
    return json.dumps({'location': loc, 'temperature': temps.get(loc, 20), 'conditions': conditions.get(loc, 'unknown')})


@trace_agent('pydantic-weather', agent_version='v1.0', workload_type='weather')
async def instrumented_run(query: str) -> str:
    with plan_span('processing weather query'):
        result = await weather_agent.run(query, model_settings={'max_tokens': 100, 'temperature': 0.1})

    output = str(result.output)
    span = otel_trace.get_current_span()
    set_output(span, output)
    return output


async def main():
    queries = [
        "Weather in London?", "Is it raining in Paris?", "Tokyo weather please",
        "New York forecast", "San Francisco conditions", "Berlin temperature",
        "Sydney weather", "Mumbai heat?", "London vs Paris weather",
        "What's the weather like today?",
    ]
    for i in range(100):
        q = random.choice(queries)
        if i % 7 == 0:
            q = 'Tell me about the meaning of life'
        try:
            result = await instrumented_run(q)
            if i % 10 == 0:
                print(f'[{i+1:3d}] {q[:40]:40s} → {str(result)[:80]}', flush=True)
        except Exception as e:
            print(f'[{i+1:3d}] ERROR: {e}', flush=True)
        await asyncio.sleep(0.1)

    print('Done: 100 traces generated')
    await asyncio.sleep(2)

asyncio.run(main())
