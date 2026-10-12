"""Agent registry and the capability + health based supervisor."""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from agentmesh.agents.analyst import AnalystAgent
from agentmesh.agents.base import Agent
from agentmesh.agents.planner import PlannerAgent
from agentmesh.agents.retrieval_agent import RetrievalAgent
from agentmesh.agents.summarizer import SummarizerAgent
from agentmesh.agents.verifier import VerifierAgent


class NoHealthyAgent(RuntimeError):
    """Raised when no registered agent can serve a capability healthily."""

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"no healthy agent available for capability '{capability}'")


class AgentRegistry:
    """Maps capabilities to agents and chooses among candidates."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        self._agents[agent.name] = agent

    def get(self, name: str) -> Agent:
        return self._agents[name]

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def for_capability(self, capability: str) -> list[Agent]:
        return [a for a in self._agents.values() if capability in a.capabilities]

    def by_name(self, name: str) -> Agent | None:
        return self._agents.get(name)


def build_default_agent_registry() -> AgentRegistry:
    """Register the four built-in agents."""

    registry = AgentRegistry()
    registry.register(PlannerAgent())
    registry.register(RetrievalAgent())
    registry.register(AnalystAgent())
    registry.register(VerifierAgent())
    registry.register(SummarizerAgent())
    return registry


@dataclass
class AgentHealth:
    """Rolling health statistics for one agent."""

    name: str
    capabilities: tuple[str, ...] = ()
    success_count: int = 0
    failure_count: int = 0
    last_heartbeat: float = 0.0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=200))

    @property
    def total(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        return self.success_count / self.total if self.total else 1.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[index]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": list(self.capabilities),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": self.success_rate,
            "p95_latency_ms": self.p95_latency_ms,
            "last_heartbeat": self.last_heartbeat,
        }


class HealthTracker:
    """Rolling agent health, optionally mirrored to Redis for cross-process use.

    The in-memory snapshot is authoritative for routing; every heartbeat and
    outcome is also published to Redis (best effort) so other processes can see
    liveness. This keeps routing deterministic and testable offline while
    satisfying the Redis heartbeat requirement.
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        *,
        stale_after_s: float = 60.0,
        min_success_rate: float = 0.5,
        min_samples: int = 1,
    ) -> None:
        self._state: dict[str, AgentHealth] = {}
        self._redis = redis_client
        self.stale_after_s = stale_after_s
        self.min_success_rate = min_success_rate
        self.min_samples = min_samples

    def _entry(self, name: str, capabilities: tuple[str, ...] = ()) -> AgentHealth:
        entry = self._state.get(name)
        if entry is None:
            entry = AgentHealth(name=name, capabilities=capabilities)
            self._state[name] = entry
        elif capabilities and not entry.capabilities:
            entry.capabilities = capabilities
        return entry

    async def heartbeat(self, name: str, capabilities: tuple[str, ...] = ()) -> None:
        entry = self._entry(name, capabilities)
        entry.last_heartbeat = time.time()
        await self._mirror(entry)

    async def record(self, name: str, *, ok: bool, latency_ms: float = 0.0) -> None:
        entry = self._entry(name)
        if ok:
            entry.success_count += 1
            entry.latencies_ms.append(latency_ms)
        else:
            entry.failure_count += 1
        entry.last_heartbeat = time.time()
        await self._mirror(entry)

    async def _mirror(self, entry: AgentHealth) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hset("agentmesh:health", entry.name, json.dumps(entry.to_json()))
            await self._redis.set("agentmesh:health:ts", str(time.time()))
        except Exception:  # noqa: BLE001 - mirroring is best effort
            pass

    def snapshot(self, name: str) -> AgentHealth | None:
        return self._state.get(name)

    def all(self) -> list[AgentHealth]:
        return list(self._state.values())

    def is_stale(self, name: str, now: float | None = None) -> bool:
        entry = self._state.get(name)
        if entry is None:
            return False
        now = now if now is not None else time.time()
        return bool(entry.last_heartbeat) and now - entry.last_heartbeat > self.stale_after_s

    def meets_quality(self, name: str) -> bool:
        entry = self._state.get(name)
        if entry is None:
            return True
        if entry.total < self.min_samples:
            return True
        return entry.success_rate >= self.min_success_rate

    def is_healthy(self, name: str, now: float | None = None) -> bool:
        return not self.is_stale(name, now) and self.meets_quality(name)


class Supervisor:
    """Selects the healthiest agent that advertises a capability.

    Stale agents are never chosen. Agents whose rolling success rate is below
    the threshold are skipped when a healthier candidate exists, but are still
    used as a last resort so retries can make progress.
    """

    def __init__(self, registry: AgentRegistry, health: HealthTracker) -> None:
        self.registry = registry
        self.health = health

    async def select_agent(
        self,
        capability: str,
        *,
        preferred: str | None = None,
        exclude: set[str] | None = None,
    ) -> Agent:
        exclude = exclude or set()
        candidates = [
            a
            for a in self.registry.for_capability(capability)
            if a.name not in exclude and not self.health.is_stale(a.name)
        ]
        if not candidates:
            raise NoHealthyAgent(capability)

        quality = [a for a in candidates if self.health.meets_quality(a.name)]
        pool = quality or candidates

        if preferred is not None:
            for agent in pool:
                if agent.name == preferred:
                    await self.health.heartbeat(agent.name, agent.capabilities)
                    return agent

        def score(agent: Agent) -> tuple[float, float, str]:
            health = self.health.snapshot(agent.name)
            if health is None:
                return (-1.0, 0.0, agent.name)
            return (-health.success_rate, health.p95_latency_ms, agent.name)

        chosen = sorted(pool, key=score)[0]
        await self.health.heartbeat(chosen.name, chosen.capabilities)
        return chosen
