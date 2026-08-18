"""Runtime health tracking with persisted state-transition detection."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src.utils.state_store import StateStore

logger = logging.getLogger("PropBot.Health")


@dataclass
class ComponentHealth:
    status: str = "unknown"
    reason: str = ""
    last_success_at: Optional[float] = None
    last_failure_at: Optional[float] = None
    consecutive_failures: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class HealthMonitor:
    """Tracks liveness and component availability without blocking the scan loop."""

    def __init__(
        self,
        state_store: StateStore,
        config: Optional[dict] = None,
        clock: Callable[[], float] = time.time,
    ):
        health_cfg = (config or {}).get("health", {})
        self.state_store = state_store
        self._clock = clock
        self.loop_stale_seconds = float(health_cfg.get("loop_stale_seconds", 120))
        self.data_stale_seconds = float(health_cfg.get("data_stale_seconds", 900))
        self.failure_threshold = int(health_cfg.get("failure_threshold", 3))
        self._components: dict[str, ComponentHealth] = {}
        self._transitions: list[dict[str, Any]] = []

    def record_success(
        self, component: str, metadata: Optional[dict[str, Any]] = None
    ) -> bool:
        now = self._clock()
        previous = self._component(component)
        changed = previous.status != "healthy"
        previous.status = "healthy"
        previous.reason = ""
        previous.last_success_at = now
        previous.consecutive_failures = 0
        if metadata:
            previous.metadata = dict(metadata)
        self.state_store.record_health(component, "healthy", metadata=previous.metadata)
        if changed:
            self._transitions.append(self.get_component(component))
        return changed

    def record_failure(
        self,
        component: str,
        reason: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        now = self._clock()
        previous = self._component(component)
        previous.last_failure_at = now
        previous.consecutive_failures += 1
        previous.reason = reason
        if metadata:
            previous.metadata = dict(metadata)
        new_status = (
            "unhealthy"
            if previous.consecutive_failures >= self.failure_threshold
            else "degraded"
        )
        changed = previous.status != new_status
        previous.status = new_status
        self.state_store.record_health(
            component, new_status, reason, metadata=previous.metadata
        )
        if changed:
            self._transitions.append(self.get_component(component))
        return changed

    def heartbeat(self) -> bool:
        self.state_store.set_runtime_value("last_loop_heartbeat", self._clock())
        return self.record_success("scan_loop")

    def evaluate(self) -> list[dict[str, Any]]:
        """Evaluate persisted liveness thresholds and return new transitions."""
        now = self._clock()
        before = len(self._transitions)
        loop_heartbeat = self.state_store.get_runtime_value("last_loop_heartbeat")
        if loop_heartbeat is not None and now - float(loop_heartbeat) > self.loop_stale_seconds:
            self.record_failure("scan_loop", "scan loop heartbeat is stale")

        data = self._component("market_data")
        if data.last_success_at is not None and now - data.last_success_at > self.data_stale_seconds:
            self.record_failure("market_data", "market data freshness threshold exceeded")
        return self._transitions[before:]

    def drain_transitions(self) -> list[dict[str, Any]]:
        transitions = list(self._transitions)
        self._transitions.clear()
        return transitions

    def is_healthy(self, component: str) -> bool:
        return self._component(component).status == "healthy"

    def get_component(self, component: str) -> Optional[dict[str, Any]]:
        item = self._components.get(component)
        if not item:
            return None
        return {
            "component": component,
            "status": item.status,
            "reason": item.reason,
            "last_success_at": item.last_success_at,
            "last_failure_at": item.last_failure_at,
            "consecutive_failures": item.consecutive_failures,
            "metadata": dict(item.metadata),
        }

    def summary(self) -> dict[str, Any]:
        components = [self.get_component(name) for name in sorted(self._components)]
        components = [component for component in components if component]
        unhealthy = [component for component in components if component["status"] != "healthy"]
        return {
            "status": "healthy" if not unhealthy else "degraded",
            "components": components,
            "persisted": self.state_store.get_health(),
        }

    def _component(self, component: str) -> ComponentHealth:
        if component not in self._components:
            self._components[component] = ComponentHealth()
        return self._components[component]
