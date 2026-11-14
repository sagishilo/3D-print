"""Agent base class rules that every agent must keep."""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import printfarm.agents as agents_pkg
from printfarm.agents.base import Agent


def _agent_classes() -> list[type[Agent]]:
    found = []
    for info in pkgutil.walk_packages(agents_pkg.__path__, f"{agents_pkg.__name__}."):
        if not info.name.endswith(".agent"):
            continue
        module = importlib.import_module(info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, Agent) and cls is not Agent and cls.__module__ == module.__name__:
                found.append(cls)
    return found


def test_agents_do_not_reuse_names_the_base_sets_per_instance():
    # An attribute set in Agent.__init__ hides a subclass method of the same name: in Phase 3 a settings cache named
    # "_settings" hid FinanceAgent._settings(conn) and every ledger handler failed with "'dict' object is not callable".
    per_instance = set(vars(Agent(bus=None, db=None, settings=None, fleet=None)))
    classes = _agent_classes()
    assert {c.__name__ for c in classes} >= {"CoordinatorAgent", "TelemetryAgent", "FleetAgent", "PersonalizationAgent",
                                             "InventoryAgent", "FinanceAgent"}
    for cls in classes:
        own = set()
        for klass in cls.__mro__:
            if klass is Agent:
                break
            own |= set(vars(klass))
        assert not own & per_instance, f"{cls.__name__} defines {own & per_instance}, which Agent.__init__ overwrites"
