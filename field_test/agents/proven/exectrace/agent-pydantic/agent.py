"""PydanticAI v2 agent — weather lookup with tool, instrumented for agent-exec-trace."""
import os, asyncio, json
os.environ.setdefault('OPENAI_API_KEY', 'omlx-test')
os.environ.setdefault('OPENAI_BASE_URL', 'http://127.0.0.1:8000/v1')

from pydantic_ai import Agent, Tool, RunContext
from pydantic import BaseModel

# ── Agent definition ────────────────────────────────────────────────────
weather_agent = Agent(
    'openai:Qwen3.5-4B-4bit',
    system_prompt='You are a weather assistant. Use tools to look up weather. Be concise.',
    deps_type=str,
)

class WeatherResult(BaseModel):
    location: str
    temperature: int
    conditions: str

@weather_agent.tool
async def get_weather(ctx: RunContext[str], location: str) -> str:
    """Get current weather for a location."""
    temps = {'london': 15, 'paris': 18, 'tokyo': 22, 'nyc': 20, 'sf': 19}
    conditions = {'london': 'rainy', 'paris': 'cloudy', 'tokyo': 'sunny', 'nyc': 'windy', 'sf': 'foggy'}
    loc = location.lower()
    return json.dumps({'location': loc, 'temperature': temps.get(loc, 20), 'conditions': conditions.get(loc, 'unknown')})

# ── Run ─────────────────────────────────────────────────────────────────
async def main():
    queries = [
        "What's the weather in London?",
        "Is it raining in Paris?",
        "Compare weather in Tokyo and NYC",
    ]
    for q in queries:
        result = await weather_agent.run(q)
        print(f"Q: {q}")
        print(f"A: {result.output[:120]}")
        print()

if __name__ == '__main__':
    asyncio.run(main())