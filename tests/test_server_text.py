"""W2-D: web/src/i18n/server-text.json lists the English text the server sends (Today, schedule reasons, order
status lines, notifications) so the dashboard's Hebrew dictionary can cover it (web/tests/hebrew.spec.ts). This test
fails when that text changed without regenerating the list: run `python scripts/server_text.py`."""

from __future__ import annotations

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_the_server_text_list_is_current():
    spec = importlib.util.spec_from_file_location("server_text", ROOT / "scripts/server_text.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stored = json.loads((ROOT / "web/src/i18n/server-text.json").read_text(encoding="utf-8"))
    assert stored == mod.server_text(), "run: python scripts/server_text.py (then add the Hebrew of new lines)"
    he = json.loads((ROOT / "src/printfarm/agents/integrations/he_notify.json").read_text(encoding="utf-8"))
    assert he == mod.hebrew(stored), "run: python scripts/server_text.py"


def test_patterns_keep_values_as_placeholders():
    spec = importlib.util.spec_from_file_location("server_text", ROOT / "scripts/server_text.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import ast

    assert mod._pattern(ast.parse('f"Order {o} is waiting in {s}"').body[0].value) == "Order {0} is waiting in {1}"
    assert mod.looks_like_text("Order {0} is waiting in {1}")
    assert not mod.looks_like_text("SELECT state FROM coordinator.sagas")
    assert not mod.looks_like_text("orders.advance")
