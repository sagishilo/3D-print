"""The connectors this build knows. W2-0 ships one: a demo store that exercises the whole framework (scheduling,
backoff, mapping, health, secrets status) without any outside service. Real ones (stores, invoicing, couriers,
alert channels) are added in W2-A, W2-B and W2-D, each a class like this one, registered in REGISTRY.
W2-A: Shopify (shopify.py, on the store framework in stores.py). W2-B: Morning (morning.py, invoicing.py) and
Israel Post (israelpost.py, couriers.py).
"""

from __future__ import annotations

from printfarm.agents.integrations.framework import Connector, Record, SecretSpec, SyncContext, SyncError, SyncResult

# The demo store's fake catalogue of orders: (external id, customer, total in agorot). Each sync "sees" one more of
# them (the cursor remembers how many), so an owner can watch records arrive, and the last one changes its status
# every sync once all are in (an update, not a duplicate).
DEMO_ORDERS = (("D-1001", "Noa", 4500), ("D-1002", "Dan", 12900), ("D-1003", "Gal", 3900), ("D-1004", "Yael", 7800),
               ("D-1005", "Ori", 5400))


class DemoStore(Connector):
    id = "demo"
    kind = "demo"
    label = "Demo store (test data)"
    secrets = (SecretSpec("DEMO_STORE_TOKEN", "API token of the demo store (only for live mode)"),)
    default_interval_s = 60

    async def sync(self, ctx: SyncContext) -> SyncResult:
        if ctx.settings.get("fail"):
            raise SyncError("the demo store is set to fail (settings.fail): this tests the retries")
        if ctx.mode == "live":
            # A real connector would call its service here with ctx.secrets; the demo has none to call.
            raise SyncError("the demo store has no live service: use fake mode")
        seen = min(int(ctx.cursor.get("seen", 0)) + 1, len(DEMO_ORDERS))
        runs = int(ctx.cursor.get("runs", 0)) + 1
        records = []
        for i, (ext, customer, total) in enumerate(DEMO_ORDERS[:seen]):
            status = "paid" if i < seen - 1 or seen < len(DEMO_ORDERS) else ("paid" if runs % 2 else "fulfilled")
            records.append(Record("order", ext, f"demo-{ext.lower()}", {"customer": customer, "total": total,
                                                                         "status": status}))
        return SyncResult(records, {"seen": seen, "runs": runs})


def _registry() -> dict[str, Connector]:
    from printfarm.agents.integrations.israelpost import IsraelPost
    from printfarm.agents.integrations.morning import Morning
    from printfarm.agents.integrations.shopify import Shopify

    return {c.id: c for c in (DemoStore(), Shopify(), Morning(), IsraelPost())}


REGISTRY: dict[str, Connector] = _registry()
