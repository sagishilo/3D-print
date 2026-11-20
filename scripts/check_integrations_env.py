"""Print SET / MISSING for every secret a connector declares in integrations.env. Never prints values.

Optional by design: a missing key only matters for a connector you switch to live mode."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from printfarm.agents.integrations.connectors import REGISTRY  # noqa: E402
from printfarm.agents.integrations.framework import read_env_file, secrets_status  # noqa: E402

env = ROOT / "integrations.env"
if not env.exists():
    print("not set  integrations.env (optional: copy integrations.env.example when you connect a service)")
values = read_env_file(env)
for c in REGISTRY.values():
    status = secrets_status(c.secrets, values)
    print(f"{c.label} [{c.id}]")
    for s in c.secrets:
        print(f"  {'SET    ' if status[s.key] else 'MISSING'}  {s.key}  {s.label}{'' if s.required else ' (optional)'}")
known = {s.key for c in REGISTRY.values() for s in c.secrets}
for k in sorted(set(values) - known):
    print(f"unknown  {k} (no connector uses it: a typo?)")
sys.exit(0)
