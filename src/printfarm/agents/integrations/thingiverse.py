"""Thingiverse model lookup (Import from link): the one model site with a free, documented API. Given a thing id it
pulls the name, designer, licence, picture and file names, so Catalog › Import from link can fill the form; the
operator still downloads the file and checks the licence. Off by default; live mode needs a free app token from
https://www.thingiverse.com/apps/create (Web App) in integrations.env as THINGIVERSE_APP_TOKEN.

MakerWorld and Printables have no public API (MakerWorld's pages answer automated requests with 403 and its internal
API needs a logged-in token; Printables only has an undocumented internal GraphQL API), so for them the farm stores
and opens the link and reads the details from the downloaded 3MF instead. Nothing here scrapes a page.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx

from printfarm.agents.integrations.framework import Connector, SecretSpec, SyncContext, SyncError, SyncResult

API = "https://api.thingiverse.com"
TIMEOUT_S = 10.0

# What fake mode answers (tests, and trying the flow without a token): thing 4321.
FAKE_THING = {"name": "Fidget spinner (fake)", "creator": {"name": "test-designer"}, "license": "Creative Commons - Attribution",
              "public_url": "https://www.thingiverse.com/thing:4321", "thumbnail": "https://cdn.thingiverse.example/4321.jpg"}
FAKE_FILES = [{"name": "spinner.3mf"}, {"name": "spinner_body.stl"}]


class Thingiverse(Connector):
    id = "thingiverse"
    kind = "models"
    label = "Thingiverse (model details from a link)"
    secrets = (SecretSpec("THINGIVERSE_APP_TOKEN", "App token (thingiverse.com/apps/create, a Web App; free)"),)
    default_interval_s = 86400            # nothing to sync: the lookup runs when someone pastes a link

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def sync(self, ctx: SyncContext) -> SyncResult:
        return SyncResult([], dict(ctx.cursor))

    async def lookup(self, ctx: SyncContext, thing_id: str) -> dict:
        """Name, designer, licence, picture and file names of a thing. Raises SyncError with what went wrong."""
        if not thing_id.isdigit():
            raise SyncError("a Thingiverse thing id is a number")
        if ctx.mode != "live":
            return _details(FAKE_THING | {"public_url": f"https://www.thingiverse.com/thing:{thing_id}"}, FAKE_FILES)
        token = ctx.secrets.get("THINGIVERSE_APP_TOKEN", "")
        if not token:
            raise SyncError("live mode needs THINGIVERSE_APP_TOKEN in integrations.env")
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_S)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            thing = await client.get(f"{API}/things/{thing_id}", headers=headers)
            files = await client.get(f"{API}/things/{thing_id}/files", headers=headers) if thing.status_code == 200 else None
        except httpx.HTTPError as exc:
            raise SyncError(f"Thingiverse could not be reached ({type(exc).__name__})") from None   # never the URL
        finally:
            if self._client is None:
                await client.aclose()
        if thing.status_code in (401, 403):
            raise SyncError("Thingiverse refused the app token: check THINGIVERSE_APP_TOKEN")
        if thing.status_code == 404:
            raise SyncError(f"Thingiverse has no thing {thing_id}")
        if thing.status_code != 200:
            raise SyncError(f"Thingiverse answered {thing.status_code}")
        try:
            body = thing.json()
            listed = files.json() if files is not None and files.status_code == 200 else []
        except ValueError:
            raise SyncError("Thingiverse sent something that is not JSON") from None
        return _details(body if isinstance(body, Mapping) else {}, listed if isinstance(listed, list) else [])


def _text(v: object, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def _details(thing: Mapping, files: list) -> dict:
    creator = thing.get("creator")
    names = [_text(f.get("name"), 120) for f in files if isinstance(f, Mapping) and f.get("name")][:30]
    thumb = _text(thing.get("thumbnail"), 500)
    return {"title": _text(thing.get("name"), 120),
            "designer": _text(creator.get("name") if isinstance(creator, Mapping) else "", 120),
            "licence": _text(thing.get("license"), 120),
            "image_url": thumb if thumb.startswith("https://") else "",
            "files": names, "has_3mf": any(n.lower().endswith(".3mf") for n in names)}
