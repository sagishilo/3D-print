"""The Thingiverse lookup (Import from link): fake mode answers test data, live mode calls the public API with the
app token and turns its answer into title, designer, licence, picture and file names; errors never show the token."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from printfarm.agents.integrations.connectors import REGISTRY
from printfarm.agents.integrations.framework import SyncContext, SyncError
from printfarm.agents.integrations.thingiverse import Thingiverse

TOKEN = "tv-secret-token-123"


def ctx(mode: str, token: str | None = TOKEN) -> SyncContext:
    return SyncContext(mode=mode, cursor={}, settings={}, secrets={"THINGIVERSE_APP_TOKEN": token} if token else {},
                       now=datetime(2026, 11, 22, tzinfo=UTC))


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_it_is_registered_off_by_default_with_its_token_in_integrations_env():
    c = REGISTRY["thingiverse"]
    assert c.kind == "models" and [s.key for s in c.secrets] == ["THINGIVERSE_APP_TOKEN"]


async def test_fake_mode_answers_test_data_without_the_network():
    got = await Thingiverse(client(lambda r: pytest.fail("no request in fake mode"))).lookup(ctx("fake", None), "4321")
    assert got["title"].endswith("(fake)") and got["has_3mf"] is True


async def test_live_mode_reads_the_thing_and_its_files():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers["authorization"]))
        if request.url.path == "/things/4321":
            return httpx.Response(200, json={"name": "  Card  tower ", "creator": {"name": "maker"},
                                             "license": "Creative Commons - Attribution - Non-Commercial",
                                             "thumbnail": "https://cdn.thingiverse.com/x.jpg"})
        return httpx.Response(200, json=[{"name": "tower.STL"}, {"name": "tower.3mf"}, {"bad": 1}])

    got = await Thingiverse(client(handler)).lookup(ctx("live"), "4321")
    assert got == {"title": "Card tower", "designer": "maker", "licence": "Creative Commons - Attribution - Non-Commercial",
                   "image_url": "https://cdn.thingiverse.com/x.jpg", "files": ["tower.STL", "tower.3mf"], "has_3mf": True}
    assert seen == [("/things/4321", f"Bearer {TOKEN}"), ("/things/4321/files", f"Bearer {TOKEN}")]


@pytest.mark.parametrize(("status", "message"), [(401, "refused the app token"), (404, "no thing 4321"), (500, "answered 500")])
async def test_live_errors_say_what_went_wrong(status, message):
    with pytest.raises(SyncError, match=message):
        await Thingiverse(client(lambda r: httpx.Response(status))).lookup(ctx("live"), "4321")


async def test_without_a_token_or_network_the_token_never_shows():
    with pytest.raises(SyncError, match="needs THINGIVERSE_APP_TOKEN"):
        await Thingiverse(client(lambda r: httpx.Response(200))).lookup(ctx("live", None), "4321")

    def down(request):
        raise httpx.ConnectError(f"cannot reach {request.url} with {TOKEN}")

    with pytest.raises(SyncError) as err:
        await Thingiverse(client(down)).lookup(ctx("live"), "4321")
    assert TOKEN not in str(err.value) and "could not be reached" in str(err.value)
    # a picture that is not https is dropped (it would be shown in the dashboard)
    tv = Thingiverse(client(lambda r: httpx.Response(200, json={"name": "x", "thumbnail": "javascript:alert(1)"}
                                                     if r.url.path == "/things/1" else [])))
    assert (await tv.lookup(ctx("live"), "1"))["image_url"] == ""
