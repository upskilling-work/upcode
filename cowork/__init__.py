"""Upcode — a coding agent over an OpenAI-compatible API."""

from .agent import CoworkAgent, AgentConfig, Event
from .tools import Tool, tool, ToolRegistry
from .manager import Orchestrator, Agent, AgentRegistry
from .agents import default_agents, make_agent, load_agents
from .skills import Skill, load_skills
from .models import ModelProfile, load_models

__all__ = [
    "CoworkAgent",
    "AgentConfig",
    "Event",
    "Tool",
    "tool",
    "ToolRegistry",
    "Orchestrator",
    "Agent",
    "AgentRegistry",
    "default_agents",
    "make_agent",
    "load_agents",
    "Skill",
    "load_skills",
    "ModelProfile",
    "load_models",
]
__version__ = "0.2.0"
