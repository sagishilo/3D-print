"""Where a catalog product comes from (pure, unit-tested in tests/test_sources.py): the model page it was taken from,
its designer and licence, and what that licence allows. Shared by the gateway (Import from link), the personalization
agent (it keeps them on the product) and the integrations agent (the Thingiverse lookup).

* ``parse_model_url``: a MakerWorld, Printables or Thingiverse model page -> (site, model id, canonical URL). Only
  these hosts and https: the link is stored and opened in the operator's own browser, never fetched by the farm
  (MakerWorld and Printables have no public API; their pages answer automated requests with 403 or need a login).
* ``licence_check``: the licence text a 3MF or a model page states -> may prints be sold? Known non-commercial
  licences are "no"; an unknown or missing licence is "unknown" (an own design has none) and is the owner's call.
* ``trademark_terms``: brand words in a name or title (Pokémon, Nintendo, Disney...): a licence for the file is not
  a licence for the brand.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URL = 500
SITES = {"makerworld": "MakerWorld", "printables": "Printables", "thingiverse": "Thingiverse"}

_HOSTS = {
    "makerworld.com": "makerworld", "www.makerworld.com": "makerworld",
    "printables.com": "printables", "www.printables.com": "printables",
    "thingiverse.com": "thingiverse", "www.thingiverse.com": "thingiverse",
}
_PATHS = {
    "makerworld": re.compile(r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?models/(\d{1,12})(?:[-/#?].*)?$", re.I),
    "printables": re.compile(r"^/(?:[a-z]{2}/)?model/(\d{1,12})(?:-[^/]*)?(?:/.*)?$", re.I),
    "thingiverse": re.compile(r"^/thing:(\d{1,12})(?:/.*)?$", re.I),
}
_CANONICAL = {
    "makerworld": "https://makerworld.com/en/models/{id}",
    "printables": "https://www.printables.com/model/{id}",
    "thingiverse": "https://www.thingiverse.com/thing:{id}",
}


@dataclass(frozen=True)
class ModelLink:
    site: str              # makerworld | printables | thingiverse
    model_id: str
    url: str               # canonical page URL (what is stored)

    @property
    def site_name(self) -> str:
        return SITES[self.site]


def parse_model_url(raw: str) -> ModelLink:
    """The model page a link points at. Raises ValueError with a message for the dashboard."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("paste the link of the model page")
    if len(text) > MAX_URL:
        raise ValueError(f"the link is longer than {MAX_URL} characters")
    scheme = re.match(r"^([a-z][a-z0-9+.-]*):", text, re.I)
    if scheme and not text.lower().startswith(("http://", "https://")) and "." not in scheme.group(1):
        raise ValueError("only https links to a model page")            # javascript:, data:, file:, ftp: ...
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise ValueError("a model page link has no user, password or port") from None
    if parts.scheme not in ("https", "http"):
        raise ValueError("only https links to a model page")
    if parts.username or parts.password or port:
        raise ValueError("a model page link has no user, password or port")
    site = _HOSTS.get((parts.hostname or "").lower())
    if site is None:
        raise ValueError("a MakerWorld, Printables or Thingiverse model page, please")
    m = _PATHS[site].match(parts.path or "/")
    if not m:
        raise ValueError(f"this {SITES[site]} link is not a model page")
    model_id = str(int(m.group(1)))
    return ModelLink(site, model_id, _CANONICAL[site].format(id=model_id))


# ---------------------------------------------------------------- licences

@dataclass(frozen=True)
class LicenceCheck:
    status: str            # commercial | non_commercial | unknown
    label: str             # short name for the dashboard


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# Order matters: the first rule that matches decides (non-commercial terms before the plain "BY" ones).
_RULES: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"\bstandard digital file\b"), "non_commercial", "Standard Digital File License (personal use)"),
    (re.compile(r"\bexclusive\b"), "non_commercial", "MakerWorld Exclusive (no commercial use)"),
    (re.compile(r"\bpersonal use\b|\bnon commercial\b|\bnoncommercial\b"), "non_commercial", "Personal use only"),
    (re.compile(r"\b(cc )?by nc( nd| sa)?\b|\bcreative commons attribution non ?commercial\b"),
     "non_commercial", "CC BY-NC (non-commercial)"),
    (re.compile(r"\bcommercial (licen[cs]e|use allowed|membership)\b"), "commercial", "Commercial licence"),
    (re.compile(r"\bcc0\b|\bpublic domain\b|\bcc zero\b"), "commercial", "CC0 / public domain"),
    (re.compile(r"\b(cc )?by nd\b"), "commercial", "CC BY-ND (credit, no changes)"),
    (re.compile(r"\b(cc )?by sa\b"), "commercial", "CC BY-SA (credit, share alike)"),
    (re.compile(r"^(cc )?by( \d(\s?\d)?)?$|\bcreative commons attribution\b(?! non)"), "commercial", "CC BY (credit)"),
    (re.compile(r"\bgpl\b|\blgpl\b|\bbsd\b|\bmit\b"), "commercial", "Open source licence"),
)


def licence_check(text: str | None) -> LicenceCheck:
    """May prints made from this file be sold, as far as its licence text says (never legal advice)."""
    t = _norm(text or "")
    if not t:
        return LicenceCheck("unknown", "No licence stated")
    for rule, status, label in _RULES:
        if rule.search(t):
            return LicenceCheck(status, label)
    return LicenceCheck("unknown", (text or "").strip()[:60])


# ---------------------------------------------------------------- trademarks

# Brand words that need the brand owner's permission on a product sold, whatever the file's licence.
TRADEMARKS = ("pokemon", "pikachu", "charizard", "poke ball", "pokeball", "nintendo", "mario", "zelda", "disney",
              "marvel", "star wars", "lego", "minecraft", "hello kitty", "harry potter", "batman", "spider man",
              "spiderman", "fortnite", "yu gi oh", "magic the gathering")


def trademark_terms(*texts: str | None) -> list[str]:
    """The brand words in these texts (name, title, file name), as found, in list order."""
    hay = " " + " ".join(_norm(t or "") for t in texts) + " "
    return [t for t in TRADEMARKS if f" {t} " in hay or (" " not in t and re.search(rf"\b{t}s?\b", hay))]
