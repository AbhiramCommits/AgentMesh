"""Runtime bundle of collaborators used when executing a workflow."""

from __future__ import annotations

from dataclasses import dataclass

from agentmesh.config import get_settings
from agentmesh.engine.registry import (
    AgentRegistry,
    HealthTracker,
    Supervisor,
    build_default_agent_registry,
)
from agentmesh.llm import ModelProvider, get_provider
from agentmesh.tools.base import ToolRegistry
from agentmesh.tools.runtime import get_registry


@dataclass
class Runtime:
    """Bundled provider, tool registry, agents, and health tracker."""

    provider: ModelProvider
    tools: ToolRegistry
    agents: AgentRegistry
    health: HealthTracker
    supervisor: Supervisor
    default_model: str = "fake-model"

    @classmethod
    def build(
        cls,
        provider: ModelProvider | None = None,
        tools: ToolRegistry | None = None,
        health: HealthTracker | None = None,
        agents: AgentRegistry | None = None,
    ) -> Runtime:
        settings = get_settings()
        provider = provider or get_provider()
        tools = tools or get_registry()
        agents = agents or build_default_agent_registry()
        health = health or HealthTracker()
        return cls(
            provider=provider,
            tools=tools,
            agents=agents,
            health=health,
            supervisor=Supervisor(agents, health),
            default_model=settings.model,
        )
