"""Example: create your own tool and chat with the agent.

Run with:  python examples/custom_tool.py
"""

from cowork import CoworkAgent, ToolRegistry, tool
from cowork.builtin_tools import default_registry
from cowork.models import load_models


@tool
def weather(city: str) -> str:
    """Return the weather for a city (fictional example)."""
    fake = {"são paulo": "23°C, partly cloudy", "rio": "30°C, sunny"}
    return fake.get(city.lower(), f"No data for {city}.")


def main() -> None:
    registry: ToolRegistry = default_registry()
    registry.add(weather)

    agent = CoworkAgent(tools=registry)

    # Model/endpoint/key come from models.json (there are no env-based defaults).
    models = load_models()
    if not models:
        raise SystemExit("Set up a models.json (see conf/models.json).")
    p = next(iter(models.values()))
    agent.reconfigure(model=p.model, base_url=p.base_url, api_key=p.api_key,
                      api=p.api, max_output=p.max_output,
                      context_window=p.context_window, temperature=p.temperature)

    # Single question
    print(agent.send("What's the weather in São Paulo and what time is it?"))


if __name__ == "__main__":
    main()
