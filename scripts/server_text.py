"""The English text the server sends to the dashboard (Today items, schedule reasons, order status lines,
notification titles, store problems, parcel refusals), as patterns ("{0} store orders wait for you"), written to
web/src/i18n/server-text.json. The dashboard's Hebrew dictionary must cover every one (web/tests/hebrew.spec.ts);
tests/test_server_text.py checks this file is current.

    python scripts/server_text.py           # rewrite the file
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = [
    "gateway/app/today.py", "gateway/app/schedule.py", "gateway/app/orders.py",
    "src/printfarm/agents/integrations/alerts.py", "src/printfarm/agents/integrations/stores.py",
    "src/printfarm/agents/integrations/couriers.py", "src/printfarm/agents/integrations/agent.py",
    "src/printfarm/agents/fleet/policy.py", "src/printfarm/agents/fleet/maintenance.py",
    # W2-D: settings labels and help, connector settings, inventory alerts, trend suggestions, health, saga refusals
    "src/printfarm/contracts/config_keys.py", "src/printfarm/agents/integrations/shopify.py",
    "src/printfarm/agents/integrations/morning.py", "src/printfarm/agents/integrations/israelpost.py",
    "src/printfarm/agents/integrations/telegram.py",
    "src/printfarm/agents/integrations/thingiverse.py", "src/printfarm/agents/integrations/channels.py",
    "src/printfarm/agents/integrations/connectors.py", "src/printfarm/agents/inventory/agent.py",
    "src/printfarm/agents/inventory/model.py", "src/printfarm/agents/trend/agent.py", "gateway/app/analytics.py",
    "gateway/app/system.py", "src/printfarm/agents/coordinator/saga.py",
]
LOG_CALLS = {"debug", "info", "warning", "error", "exception", "critical", "log"}
DATA_FUNCTIONS = {"fake_orders"}           # test data, not UI text
OUT = ROOT / "web/src/i18n/server-text.json"
HE_DIR = ROOT / "web/src/i18n/he"
# The Hebrew of the server's text, for messages the integrations agent sends itself (Telegram, e-mail)
HE_OUT = ROOT / "src/printfarm/agents/integrations/he_notify.json"
# literals that are not text for people (SQL, keys, ids)
NOT_TEXT = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|FROM|WHERE|JOIN|ORDER BY|ON CONFLICT|RETURNING|VALUES)\b|^[a-z_.:]+$|/api/|%s")


def _pattern(node: ast.AST) -> str | None:
    """A string literal or f-string as a pattern: {expr} becomes {n}."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out, n = [], 0
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(str(v.value))
            else:
                out.append(f"{{{n}}}")
                n += 1
        return "".join(out)
    return None


def looks_like_text(p: str) -> bool:
    words = re.findall(r"[A-Za-z][a-z']+", re.sub(r"\{\d+\}", " ", p))
    return (len(words) >= 2 and " " in p.strip() and not re.search(r"[|^$\\]|^(query|mutation) |^[A-Z][a-z]+-[A-Z]", p)
            and not re.search(r"%%|LIKE |interval '", p) and not NOT_TEXT.search(p) and "_" not in p and "/" not in p.split(" ")[0]
            and not re.search(r"[A-Z][a-z]+/[A-Z]", p) and not re.fullmatch(r"[a-z]+(-[a-z]+)+", p)
            and "tick failed" not in p)


def server_text() -> list[str]:
    found: list[str] = []
    for rel in SOURCES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        skip: set[ast.AST] = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name in DATA_FUNCTIONS:
                skip |= set(ast.walk(fn))
        for node in ast.walk(tree):
            if node in skip:
                continue
            parent = parents.get(node)
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr in LOG_CALLS:
                continue                                  # a log line, not text for people
            if isinstance(node, ast.Constant) and isinstance(parents.get(node), ast.JoinedStr):
                continue                                  # a piece of an f-string: the f-string is taken whole
            if isinstance(parents.get(node), ast.Expr) and isinstance(node, ast.Constant):
                continue                                  # a docstring
            p = _pattern(node)
            if p is None:
                continue
            p = re.sub(r"\s+", " ", p).strip()
            if looks_like_text(p) and p not in found:
                found.append(p)
    return sorted(found)


def hebrew(patterns: list[str]) -> dict[str, str]:
    """The dashboard dictionary's Hebrew for these patterns (those it has)."""
    he: dict[str, str] = {}
    for f in sorted(HE_DIR.glob("*.json")):
        he.update(json.loads(f.read_text(encoding="utf-8")))
    return {p: he[p] for p in patterns if p in he}


def main() -> int:
    patterns = server_text()
    OUT.write_text(json.dumps(patterns, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    he = hebrew(patterns)
    HE_OUT.write_text(json.dumps(he, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(patterns)} patterns; {HE_OUT.relative_to(ROOT)}: {len(he)} in Hebrew")
    return 0


if __name__ == "__main__":
    sys.exit(main())
