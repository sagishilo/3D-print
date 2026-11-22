"""Where a product comes from: model page links, what a licence allows, brand words (contracts/sources.py), and the
design details a MakerWorld / Bambu Studio 3MF carries (personalization/catalog.py design_meta)."""

from __future__ import annotations

import pytest
from test_catalog import NS, cube, three_mf

from printfarm.contracts.sources import licence_check, parse_model_url, trademark_terms


@pytest.mark.parametrize(("url", "site", "model_id", "canonical"), [
    ("https://makerworld.com/en/models/111937", "makerworld", "111937", "https://makerworld.com/en/models/111937"),
    ("https://makerworld.com/models/111937-pokemon-card-stand?from=search#profileId-1", "makerworld", "111937",
     "https://makerworld.com/en/models/111937"),
    ("makerworld.com/zh/models/42", "makerworld", "42", "https://makerworld.com/en/models/42"),
    ("https://www.printables.com/model/123456-fidget-spinner", "printables", "123456", "https://www.printables.com/model/123456"),
    ("https://www.printables.com/de/model/99/files", "printables", "99", "https://www.printables.com/model/99"),
    ("https://www.thingiverse.com/thing:4321/files", "thingiverse", "4321", "https://www.thingiverse.com/thing:4321"),
    ("http://thingiverse.com/thing:007", "thingiverse", "7", "https://www.thingiverse.com/thing:7"),
])
def test_model_page_links_are_recognised(url, site, model_id, canonical):
    link = parse_model_url(url)
    assert (link.site, link.model_id, link.url) == (site, model_id, canonical)


@pytest.mark.parametrize(("url", "message"), [
    ("", "paste the link"),
    ("https://evil.example/models/1", "MakerWorld, Printables or Thingiverse"),
    ("https://makerworld.com.evil.example/en/models/1", "MakerWorld, Printables or Thingiverse"),
    ("https://makerworld.com/en/u/someone", "not a model page"),
    ("ftp://makerworld.com/en/models/1", "only https"),
    ("javascript:alert(1)", "only https"),
    ("data:text/html,hi", "only https"),
    ("https://user:pw@makerworld.com/en/models/1", "no user, password or port"),
    ("https://makerworld.com:8443/en/models/1", "no user, password or port"),
    ("https://www.thingiverse.com/thing:abc", "not a model page"),
    ("https://makerworld.com/en/models/" + "1" * 600, "longer than"),
])
def test_anything_else_is_refused_with_a_reason(url, message):
    with pytest.raises(ValueError, match=message):
        parse_model_url(url)


@pytest.mark.parametrize(("text", "status"), [
    ("Standard Digital File License", "non_commercial"),
    ("BY-NC", "non_commercial"),
    ("BY-NC-ND", "non_commercial"),
    ("BY-NC-SA", "non_commercial"),
    ("Creative Commons - Attribution - Non-Commercial", "non_commercial"),
    ("MakerWorld Exclusive License", "non_commercial"),
    ("BY", "commercial"),
    ("BY-SA", "commercial"),
    ("BY-ND", "commercial"),
    ("CC0", "commercial"),
    ("Creative Commons - Public Domain Dedication", "commercial"),
    ("Creative Commons - Attribution", "commercial"),
    ("Commercial License", "commercial"),
    ("", "unknown"),
    (None, "unknown"),
    ("Some custom terms", "unknown"),
])
def test_what_a_licence_allows(text, status):
    assert licence_check(text).status == status


def test_brand_words_are_found_in_names_and_titles():
    assert trademark_terms("Modular Pokemon PSA Card Charizard Stand Holder") == ["pokemon", "charizard"]
    assert trademark_terms("Top Loader Pokémon card Display") == ["pokemon"]
    assert trademark_terms("Poke Ball Toploader Stand (3-colour)", None) == ["poke ball"]
    assert trademark_terms("Fidget spinner", "Card display tower") == []
    assert trademark_terms("Pokemons for sale") == ["pokemon"]
    assert trademark_terms("Remarios") == []                       # a word inside another word is no brand


RELS = "_rels/.rels"


def maker_3mf(meta: dict[str, str]) -> bytes:
    md = "".join(f'<metadata name="{k}">{v}</metadata>' for k, v in meta.items())
    return three_mf({"3D/3dmodel.model": f'<model {NS}>{md}<resources>{cube("1", 10, 10, 1)}</resources>'
                                         '<build><item objectid="1"/></build></model>'})


def test_a_makerworld_3mf_says_who_designed_it_and_under_which_licence():
    from printfarm.agents.personalization.catalog import design_meta, inspect_3mf

    data = maker_3mf({"Title": "Top Loader Pok&#233;mon card Display", "Designer": "SilentYetiDesigns",
                      "License": "Standard Digital File License", "Origin": "original",
                      "DesignModelId": "US39916e4f4a1a9c", "Application": "BambuStudio-01.10.01.50"})
    meta = design_meta(data)
    assert meta == {"title": "Top Loader Pokémon card Display", "designer": "SilentYetiDesigns",
                    "licence": "Standard Digital File License", "origin": "original", "design_id": "US39916e4f4a1a9c",
                    "licence_status": "non_commercial", "licence_label": "Standard Digital File License (personal use)",
                    "trademarks": ["pokemon"]}
    assert inspect_3mf(data).as_dict()["design"] == meta


def test_a_3mf_without_design_details_says_nothing():
    from printfarm.agents.personalization.catalog import design_meta

    assert design_meta(maker_3mf({"Application": "PrusaSlicer"})) is None
    assert design_meta(b"not a zip") is None
