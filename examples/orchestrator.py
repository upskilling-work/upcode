"""Example: orchestrator delegating to specialist agents.

Run with:  python examples/orchestrator.py

The orchestrator splits the task, invokes the agents (`programmer`,
`writer`, etc.) and synthesizes the final response.
"""

from cowork import Orchestrator, default_agents


def main() -> None:
    def log(agent_name: str, task: str) -> None:
        print(f"  → delegating to {agent_name}: {task[:60]}…")

    orchestrator = Orchestrator(
        agents=default_agents(),
        on_delegate=log,
    )

    task = (
        "List the Python files in this project and write a short paragraph, "
        "in a README tone, explaining what the project does."
    )
    print(orchestrator.send(task))


if __name__ == "__main__":
    main()
