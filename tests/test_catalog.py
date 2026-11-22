"""Instant Catalog (Phase 8): 3MF templates, batch estimates, catalog order lines, the saga path, slicer failures."""

from __future__ import annotations

import io
import sys
import zipfile

import pytest

from printfarm.agents.coordinator.saga import Switches, decide, start_created
from printfarm.agents.personalization.catalog import (
    BatchItem,
    InvalidThreeMf,
    estimate_batch,
    inspect_3mf,
)
from printfarm.agents.personalization.slicer import SliceError, find_slicer, slice_3mf
from printfarm.contracts.catalog import trend_pct, valid_sku
from printfarm.contracts.messages import (
    CreateOrder,
    EnqueueJob,
    GenerateNameplate,
    ModelReady,
    PrepareCatalogItem,
    UpsertCatalogProduct,
)
from printfarm.contracts.orders import OrderLine, price_order, product_type

NS = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
PNS = 'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'


def cube(oid: str, w: float, d: float, h: float) -> str:
    vs = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x in (0, w) for y in (0, d) for z in (0, h))
    return f'<object id="{oid}" type="model"><mesh><vertices>{vs}</vertices><triangles/></mesh></object>'


def three_mf(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def plain_model(transform: str = "1 0 0 0 1 0 0 0 1 100 100 0") -> bytes:
    return three_mf({"3D/3dmodel.model": f'<model {NS}><resources>{cube("1", 40, 20, 5)}</resources>'
                                        f'<build><item objectid="1" transform="{transform}"/></build></model>'})


SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config><header><header_item key="X-BBL-Client-Type" value="slicer"/></header>
<plate><metadata key="index" value="1"/><metadata key="printer_model_id" value="C12"/>
<metadata key="prediction" value="5400"/><metadata key="weight" value="31.26"/>
<filament id="1" type="PLA" color="#000000" used_m="10.4" used_g="31.26"/></plate></config>"""


def sliced_model() -> bytes:
    """A Bambu Studio style export: the mesh in 3D/Objects, referenced as a component, plus the sliced plate."""
    return three_mf({
        "3D/Objects/object_1.model": f'<model {NS}><resources>{cube("1", 30, 30, 10)}</resources><build/></model>',
        "3D/3dmodel.model": f'<model {NS} {PNS}><resources><object id="2" type="model"><components>'
                            '<component p:path="/3D/Objects/object_1.model" objectid="1" '
                            'transform="2 0 0 0 1 0 0 0 1 0 0 0"/></components></object></resources>'
                            '<build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 128 128 0"/></build></model>',
        "Metadata/plate_1.gcode": "; sliced\nG28\n",
        "Metadata/slice_info.config": SLICE_INFO,
    })


# ---------------------------------------------------------------- 3MF inspection


def test_an_unsliced_model_gives_its_footprint_even_when_rotated():
    info = inspect_3mf(plain_model())
    assert not info.sliced and info.plates == 0 and info.objects == 1
    assert info.footprint_mm == (40, 20) and info.est_grams is None and info.est_minutes is None
    rotated = inspect_3mf(plain_model("0 1 0 -1 0 0 0 0 1 100 100 0"))      # 90 degrees about z
    assert rotated.footprint_mm == (20, 40)


def test_a_sliced_export_brings_the_slicers_time_and_weight():
    info = inspect_3mf(sliced_model())
    assert info.sliced and info.plates == 1 and info.warnings == ()
    assert info.est_minutes == 90 and info.est_grams == 31.3
    assert info.printer_model == "C12" and info.filament == "PLA"
    assert info.footprint_mm == (60, 30)            # the component's x-scale of 2 counts


@pytest.mark.parametrize(("data", "message"), [
    (b"not a zip", "not a 3MF file"),
    (three_mf({"readme.txt": "x"}), "no 3D/3dmodel.model"),
    (three_mf({"3D/3dmodel.model": f'<!DOCTYPE x [<!ENTITY a "b">]><model {NS}/>'}), "document type declarations"),
    (three_mf({"3D/3dmodel.model": f"<model {NS}><resources/><build/></model>"}), "no objects"),
    (three_mf({"3D/3dmodel.model": "<model"}), "not valid XML"),
])
def test_unusable_uploads_are_refused_with_a_reason(data, message):
    with pytest.raises(InvalidThreeMf, match=message):
        inspect_3mf(data)


def test_two_sliced_plates_are_flagged():
    data = three_mf({"3D/3dmodel.model": f'<model {NS}><resources>{cube("1", 10, 10, 1)}</resources>'
                                         '<build><item objectid="1"/></build></model>',
                     "Metadata/plate_1.gcode": "G28", "Metadata/plate_2.gcode": "G28"})
    info = inspect_3mf(data)
    assert info.plates == 2 and any("2 sliced plates" in w for w in info.warnings)


def model_settings(*plates: tuple[str, ...]) -> str:
    """Bambu Studio's per-plate object list: one <plate> per tuple of root object ids."""
    body = "".join(
        "<plate>" + "".join(f'<model_instance><metadata key="object_id" value="{o}"/>'
                            '<metadata key="instance_id" value="0"/></model_instance>' for o in objs) + "</plate>"
        for objs in plates)
    return f'<?xml version="1.0" encoding="UTF-8"?><config>{body}</config>'


def three_plate_project() -> bytes:
    """An unsliced project as MakerWorld ships it: 3 plates laid side by side, 300 mm apart, on one build area.
    Plate 1: a 60x40 part; plate 2: two 30x30 parts 100 mm apart (130x30 together); plate 3: a 150x90 part."""
    objs = cube("1", 60, 40, 5) + cube("2", 30, 30, 5) + cube("3", 30, 30, 5) + cube("4", 150, 90, 5)
    items = ('<item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 10 0"/>'
             '<item objectid="2" transform="1 0 0 0 1 0 0 0 1 310 10 0"/>'
             '<item objectid="3" transform="1 0 0 0 1 0 0 0 1 410 10 0"/>'
             '<item objectid="4" transform="1 0 0 0 1 0 0 0 1 610 310 0"/>')
    return three_mf({"3D/3dmodel.model": f"<model {NS}><resources>{objs}</resources><build>{items}</build></model>",
                     "Metadata/model_settings.config": model_settings(("1",), ("2", "3"), ("4",)),
                     "Metadata/plate_1.json": '{"bbox_all": [10, 10, 70, 50]}'})


def test_an_unsliced_multi_plate_project_is_as_large_as_its_largest_plate():
    info = inspect_3mf(three_plate_project())
    assert not info.sliced and info.objects == 4
    assert info.footprint_mm == (150, 90)       # plate 3 alone: not the 750x390 of all plates, not plate 1's 60x40
    assert any("3 plates in the project" in w for w in info.warnings)


def test_the_largest_plate_is_the_one_with_the_longest_side():
    # plate 1: two parts making 130x30 (longest side 130); plate 2: one 100x100 part (larger area, shorter side)
    objs = cube("1", 30, 30, 5) + cube("2", 30, 30, 5) + cube("3", 100, 100, 5)
    items = ('<item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
             '<item objectid="2" transform="1 0 0 0 1 0 0 0 1 100 0 0"/>'
             '<item objectid="3" transform="1 0 0 0 1 0 0 0 1 400 0 0"/>')
    data = three_mf({"3D/3dmodel.model": f"<model {NS}><resources>{objs}</resources><build>{items}</build></model>",
                     "Metadata/model_settings.config": model_settings(("1", "2"), ("3",))})
    assert inspect_3mf(data).footprint_mm == (130, 30)


def test_a_one_plate_project_keeps_its_footprint():
    data = three_mf({"3D/3dmodel.model": f'<model {NS}><resources>{cube("1", 40, 20, 5)}</resources>'
                                         '<build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 100 100 0"/></build>'
                                         "</model>",
                     "Metadata/model_settings.config": model_settings(("1",))})
    info = inspect_3mf(data)
    assert info.footprint_mm == (40, 20) and info.warnings == ()


# ---------------------------------------------------------------- batches


def item(sku, qty, *, sliced=False, size=(50, 50), grams=20.0, minutes=60.0, color="black"):
    return BatchItem(sku, qty, "PLA", color, size, grams, minutes, sliced)


def test_unsliced_pieces_share_plates_and_sliced_ones_print_alone():
    est = estimate_batch([item("HOOK-A", 6, minutes=100), item("VASE-S", 2, sliced=True, minutes=120, grams=80)],
                         {("PLA", "black"): 1000}, bed=(256, 256), max_minutes=480)
    # six 50x50 hooks fit one bed, but 6 x 100 min > 480: two plates; two sliced vases: one plate each
    assert est.plates == 4 and est.pieces == 8 and est.skus == 2
    assert est.minutes == 6 * 100 + 2 * 120 and est.grams == 6 * 20 + 2 * 80
    assert est.short == ()


def test_the_estimate_names_the_filament_that_is_short():
    est = estimate_batch([item("HOOK-A", 10, grams=50), item("TAG-W", 1, color="white")],
                         {("PLA", "black"): 170.0}, bed=(256, 256))
    black, white = est.materials
    assert (black.color, black.need_grams, black.available_grams, black.short_grams) == ("black", 500, 170, 330)
    assert (white.color, white.short_grams) == ("white", 20)            # nothing of it in stock
    assert [m.color for m in est.short] == ["black", "white"]


def test_a_piece_larger_than_the_bed_is_reported_not_planned():
    est = estimate_batch([item("SIGN-XL", 1, size=(400, 100)), item("HOOK-A", 1)], {}, bed=(256, 256))
    assert est.too_big == ("SIGN-XL",) and est.plates == 1 and est.pieces == 2
    assert [m.need_grams for m in est.materials] == [20]


def test_demand_trend():
    assert trend_pct(12, 8) == 50.0 and trend_pct(3, 4) == -25.0 and trend_pct(5, 0) is None
    assert valid_sku("KEY-HOOK-01") and not valid_sku("key-hook") and not valid_sku("A")


# ---------------------------------------------------------------- catalog lines


def test_a_catalog_line_needs_a_sku_and_its_price():
    line = OrderLine(product="catalog", sku="HOOK-A", quantity=3, unit_price_agorot=2500)
    assert price_order([line]).total == 7500 and product_type(line.product, line.sku) == "HOOK-A"
    assert product_type("nameplate") == "nameplate"
    with pytest.raises(ValueError, match="needs a SKU"):
        OrderLine(product="catalog", unit_price_agorot=100)
    with pytest.raises(ValueError, match="price comes from the catalog"):
        OrderLine(product="catalog", sku="HOOK-A")
    with pytest.raises(ValueError, match="only a catalog item has a SKU"):
        OrderLine(text="Noa", sku="HOOK-A")
    with pytest.raises(ValueError, match="unknown category"):
        UpsertCatalogProduct(sku="HOOK-A", name="Hook", category="weapons", price_agorot=100)


def batch(dispatch=True, as_lead=False) -> CreateOrder:
    return CreateOrder(order_id="stk-1", customer_name="Stock batch", channel="stock", dispatch=dispatch,
                       as_lead=as_lead, created_by="admin",
                       items=[OrderLine(product="catalog", sku="HOOK-A", quantity=2, unit_price_agorot=0),
                              OrderLine(text="Noa", unit_price_agorot=0)])


def test_a_stock_batch_is_prepared_and_scheduled_with_every_switch_off():
    off = Switches()
    d = start_created(batch(as_lead=True), off)          # a batch is never a lead
    assert d.state == "PERSONALIZING"
    prep, gen = d.outgoing
    assert isinstance(prep, PrepareCatalogItem) and (prep.item_id, prep.sku) == ("1", "HOOK-A")
    assert isinstance(gen, GenerateNameplate)
    ready = ModelReady(order_id="stk-1", item_id="1", artifact_uri="file:///c/HOOK-A.3mf", source_uri="file:///c/HOOK-A.3mf",
                       footprint_mm=(30, 30), est_grams=12, est_minutes=40, sliced=True)
    d = decide(d.state, d.context, ready, off)
    d = decide(d.state, d.context, ready.model_copy(update={"item_id": "2", "sliced": False}), off)
    jobs = [m for m in d.outgoing if isinstance(m, EnqueueJob)]
    assert d.state == "SCHEDULING" and len(jobs) == 3
    hooks = [j for j in jobs if j.product_type == "HOOK-A"]
    assert len(hooks) == 2 and all(j.whole_plate for j in hooks)
    assert [j.whole_plate for j in jobs if j.product_type == "nameplate"] == [False]


def test_a_normal_order_with_a_catalog_line_still_waits_at_the_gates():
    d = start_created(batch(dispatch=False), Switches())
    assert d.state == "RECEIVED" and d.outgoing == []


# ---------------------------------------------------------------- slicer


def test_no_slicer_and_a_failing_slicer(tmp_path, monkeypatch):
    monkeypatch.setenv("SLICER_PATH", str(tmp_path / "missing"))
    assert find_slicer() is None
    src = tmp_path / "in.3mf"
    src.write_bytes(plain_model())
    with pytest.raises(SliceError, match="the slicer failed"):            # python rejects --slice: exit code 2
        import asyncio
        asyncio.run(slice_3mf(sys.executable, src, tmp_path / "out" / "x.3mf"))


# ---------------------------------------------------------------- product images and deletion rules

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel-1" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        '<Relationship Target="/Auxiliaries/.thumbnails/thumbnail_3mf.png" Id="rel-2" '
        'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/></Relationships>')


def model_with(files: dict[str, str | bytes]) -> bytes:
    return three_mf({"3D/3dmodel.model": f'<model {NS}><resources>{cube("1", 10, 10, 1)}</resources>'
                                         '<build><item objectid="1"/></build></model>', **files})


def test_the_package_thumbnail_comes_first():
    from printfarm.agents.personalization.catalog import thumbnail_3mf

    data = model_with({"_rels/.rels": RELS, "Auxiliaries/.thumbnails/thumbnail_3mf.png": PNG,
                       "Metadata/plate_1.png": PNG + b"plate"})
    assert thumbnail_3mf(data) == (PNG, "image/png")


def test_without_a_package_thumbnail_the_plate_render_is_used():
    from printfarm.agents.personalization.catalog import thumbnail_3mf

    assert thumbnail_3mf(model_with({"Metadata/plate_1.png": JPEG})) == (JPEG, "image/jpeg")
    # the relationship names a missing file: fall back
    assert thumbnail_3mf(model_with({"_rels/.rels": RELS, "Metadata/thumbnail.png": PNG})) == (PNG, "image/png")


def test_a_thumbnail_that_is_no_picture_or_none_at_all_gives_nothing():
    from printfarm.agents.personalization.catalog import thumbnail_3mf

    assert thumbnail_3mf(model_with({"Metadata/plate_1.png": b"<svg onload=alert(1)>"})) is None
    assert thumbnail_3mf(model_with({})) is None
    assert thumbnail_3mf(b"not a zip") is None


def test_a_thumbnail_over_the_size_limit_is_skipped():
    from printfarm.agents.personalization.catalog import thumbnail_3mf
    from printfarm.contracts.catalog import MAX_IMAGE_BYTES

    big = PNG + b"\x00" * MAX_IMAGE_BYTES
    assert thumbnail_3mf(model_with({"Metadata/plate_1.png": big, "Metadata/thumbnail.png": JPEG})) == (JPEG, "image/jpeg")


@pytest.mark.parametrize(("data", "kind"), [
    (PNG, "image/png"), (JPEG, "image/jpeg"), (b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image/webp"),
    (b"GIF89a", None), (b"<svg/>", None), (b"<html>", None), (b"", None), (b"RIFF\x10\x00\x00\x00WAVE", None),
])
def test_pictures_are_recognised_by_their_bytes(data, kind):
    from printfarm.contracts.catalog import image_type

    assert image_type(data) == kind


def test_what_keeps_a_product_from_being_deleted():
    from printfarm.contracts.catalog import delete_blocker

    assert delete_blocker({"orders": 0, "stock": 0, "store_maps": 0}) is None
    one = delete_blocker({"orders": 1, "stock": 0, "store_maps": 0})
    assert one is not None and "1 order:" in one and "switch it off instead" in one
    both = delete_blocker({"orders": 3, "stock": 1, "store_maps": 2})
    assert both is not None and "3 orders" in both and "finished stock" in both and "2 store listings" in both
