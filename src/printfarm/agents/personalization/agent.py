"""Personalization Agent: turns an order line into a printable artifact, headlessly (one per order line).

* a nameplate: its text -> OpenSCAD model (nameplate.py);
* a catalog SKU: its 3MF template from the Instant Catalog (catalog.products, owned here; catalog.py).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from printfarm.agents.base import Agent, on
from printfarm.agents.personalization.catalog import InvalidThreeMf, ThreeMfInfo, inspect_3mf
from printfarm.agents.personalization.nameplate import InvalidText, NameplateSpec, generate, safe_name
from printfarm.agents.personalization.slicer import SliceError, find_slicer, slice_3mf
from printfarm.agents.telemetry.platefiles import local_path
from printfarm.contracts.catalog import UPLOAD_BUCKET
from printfarm.contracts.messages import (
    CatalogProductRejected,
    CatalogProductUpdated,
    GenerateNameplate,
    ModelFailed,
    ModelReady,
    PrepareCatalogItem,
    SliceCatalogProduct,
    UpsertCatalogProduct,
)

NO_SLICER = ("no slicer is installed next to the personalization agent: slice the template in Bambu Studio or "
             "OrcaSlicer (one plate, File > Export > Export plate sliced file) and import that 3MF for the same SKU")


class PersonalizationAgent(Agent):
    name = "personalization"

    @on("cmd.personalization.generate_nameplate")
    async def generate_nameplate(self, env, cmd: GenerateNameplate, conn):
        existing = await (
            await conn.execute("SELECT * FROM personalization.artifacts WHERE order_id = %s AND item_id = %s",
                               (cmd.order_id, cmd.item_id))
        ).fetchone()
        if existing and existing["text"] == cmd.text:  # same text: re-announce the stored result
            await self.emit(conn, self._ready(cmd.order_id, cmd.item_id, existing), cause=env)
            return
        if existing:  # the operator changed the text (retry after a failure): make a new model
            await conn.execute("DELETE FROM personalization.artifacts WHERE order_id = %s AND item_id = %s",
                               (cmd.order_id, cmd.item_id))

        spec = NameplateSpec(
            text=cmd.text, width_mm=cmd.width_mm, height_mm=cmd.height_mm,
            thickness_mm=cmd.thickness_mm, text_height_mm=cmd.text_height_mm, font=self.fleet.font,
        )
        try:
            name = safe_name(f"{cmd.order_id}-{cmd.item_id}" if cmd.item_id else cmd.order_id)
            res = await generate(spec, Path(self.settings.artifact_dir), name)
        except InvalidText as exc:
            await self.emit(conn, ModelFailed(order_id=cmd.order_id, item_id=cmd.item_id, reason=f"invalid text: {exc}"),
                            cause=env)
            return
        except RuntimeError as exc:  # tool failure: permanent for this input, a human must look
            await self.emit(conn, ModelFailed(order_id=cmd.order_id, item_id=cmd.item_id, reason=str(exc)), cause=env)
            return

        row = {
            "source_uri": res.source_path.resolve().as_uri(),
            "artifact_uri": res.artifact_path.resolve().as_uri(),
            "footprint_w": res.footprint_mm[0],
            "footprint_d": res.footprint_mm[1],
            "est_grams": res.est_grams,
            "est_minutes": res.est_minutes,
            "sliced": res.sliced,
        }
        await conn.execute(
            "INSERT INTO personalization.artifacts (order_id, item_id, text, source_uri, artifact_uri, footprint_w, "
            "footprint_d, est_grams, est_minutes, sliced) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (cmd.order_id, cmd.item_id, cmd.text, *row.values()),
        )
        await self.emit(conn, self._ready(cmd.order_id, cmd.item_id, row), cause=env)

    @staticmethod
    def _ready(order_id: str, item_id: str, row) -> ModelReady:
        return ModelReady(
            order_id=order_id,
            item_id=item_id,
            artifact_uri=row["artifact_uri"],
            source_uri=row["source_uri"],
            footprint_mm=(row["footprint_w"], row["footprint_d"]),
            est_grams=row["est_grams"],
            est_minutes=row["est_minutes"],
            sliced=row["sliced"],
        )

    # ------------------------------------------------------------ instant catalog

    def _catalog_dir(self) -> Path:
        return Path(self.settings.artifact_dir) / "catalog"

    async def _product(self, conn, sku: str):
        return await (await conn.execute("SELECT * FROM catalog.products WHERE sku = %s", (sku,))).fetchone()

    async def _reject(self, conn, env, sku: str, action: str, reason: str, by: str) -> None:
        self.log.info("catalog %s %s refused: %s", action, sku, reason)
        await self.emit(conn, CatalogProductRejected(sku=sku, action=action, reason=reason, by=by or "unknown"), cause=env)

    @on("cmd.personalization.upsert_product")
    async def upsert_product(self, env, cmd: UpsertCatalogProduct, conn):
        by = cmd.changed_by or "unknown"
        row = await self._product(conn, cmd.sku)
        file: dict = {}
        info: ThreeMfInfo | None = None
        if cmd.upload:
            data = await self.bus.blob_get(UPLOAD_BUCKET, cmd.upload)
            if data is None:
                return await self._reject(conn, env, cmd.sku, "upsert", "the upload is gone (kept 7 days): upload the 3MF again", by)
            if hashlib.sha256(data).hexdigest() != cmd.upload:
                return await self._reject(conn, env, cmd.sku, "upsert", "the upload does not match its checksum", by)
            try:
                info = await asyncio.to_thread(inspect_3mf, data)
            except InvalidThreeMf as exc:
                return await self._reject(conn, env, cmd.sku, "upsert", str(exc), by)
            if info.plates > 1:
                return await self._reject(conn, env, cmd.sku, "upsert",
                                          f"the file holds {info.plates} sliced plates: export one plate per file", by)
            if info.footprint_mm is None:
                return await self._reject(conn, env, cmd.sku, "upsert", "no model geometry could be read from the file", by)
            path = self._catalog_dir() / f"{cmd.sku}-{cmd.upload[:12]}.3mf"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.is_file():
                await asyncio.to_thread(path.write_bytes, data)
            file = {"file_uri": path.resolve().as_uri(), "file_sha256": cmd.upload,
                    "file_name": cmd.upload_name or path.name, "sliced": info.sliced,
                    "printer_model": info.printer_model, "footprint_w": info.footprint_mm[0],
                    "footprint_d": info.footprint_mm[1]}
        elif row is None:
            return await self._reject(conn, env, cmd.sku, "upsert", "a new SKU needs its 3MF template", by)

        if info is not None and info.sliced and info.est_grams and info.est_minutes:
            grams, minutes, estimated = info.est_grams, info.est_minutes, False
        elif info is None and not row["estimated"]:          # keep the slicer's numbers of the stored file
            grams, minutes, estimated = row["est_grams"], row["est_minutes"], False
        else:
            grams = cmd.est_grams or (row["est_grams"] if row and info is None else None)
            minutes = cmd.est_minutes or (row["est_minutes"] if row and info is None else None)
            if not grams or not minutes:
                return await self._reject(conn, env, cmd.sku, "upsert", "the template is not sliced: enter its grams "
                                          "and print minutes (or import a sliced 3MF)", by)
            estimated = True

        values = {"name": cmd.name, "category": cmd.category, "material": cmd.material, "color": cmd.color,
                  "price_agorot": cmd.price_agorot, "est_grams": grams, "est_minutes": minutes, "estimated": estimated,
                  "active": cmd.active, "updated_by": by, **file}
        if row is None:
            cols = [*values, "created_by"]
            await conn.execute(
                f"INSERT INTO catalog.products (sku, {', '.join(cols)}) VALUES (%s{', %s' * len(cols)})",
                (cmd.sku, *values.values(), by))
        else:
            sets = ", ".join(f"{k} = %s" for k in values)
            await conn.execute(f"UPDATE catalog.products SET {sets}, updated_at = now() WHERE sku = %s",
                               (*values.values(), cmd.sku))
        sliced = file.get("sliced", row["sliced"] if row else False)
        self.log.info("catalog %s %s (%s)", "added" if row is None else "updated", cmd.sku,
                      "sliced" if sliced else "unsliced")
        await self.emit(conn, CatalogProductUpdated(sku=cmd.sku, name=cmd.name, active=cmd.active, sliced=sliced, by=by),
                        cause=env)

    @on("cmd.personalization.slice_product")
    async def slice_product(self, env, cmd: SliceCatalogProduct, conn):
        by = cmd.requested_by or "unknown"
        row = await self._product(conn, cmd.sku)
        if row is None:
            return await self._reject(conn, env, cmd.sku, "slice", f"no SKU {cmd.sku} in the catalog", by)
        if row["sliced"]:
            return await self._reject(conn, env, cmd.sku, "slice", "the template is already sliced", by)
        slicer = find_slicer()
        if slicer is None:
            return await self._reject(conn, env, cmd.sku, "slice", NO_SLICER, by)
        src = local_path(row["file_uri"])
        out = self._catalog_dir() / f"{cmd.sku}-{row['file_sha256'][:12]}.sliced.3mf"
        try:
            info = await slice_3mf(slicer, src, out)
        except (SliceError, InvalidThreeMf) as exc:
            return await self._reject(conn, env, cmd.sku, "slice", str(exc), by)
        grams = info.est_grams or row["est_grams"]
        minutes = info.est_minutes or row["est_minutes"]
        await conn.execute(
            "UPDATE catalog.products SET file_uri = %s, sliced = true, printer_model = %s, est_grams = %s, "
            "est_minutes = %s, estimated = %s, updated_by = %s, updated_at = now() WHERE sku = %s",
            (out.resolve().as_uri(), info.printer_model, grams, minutes, not (info.est_grams and info.est_minutes), by,
             cmd.sku))
        await self.emit(conn, CatalogProductUpdated(sku=cmd.sku, name=row["name"], active=row["active"], sliced=True,
                                                    by=by), cause=env)

    @on("cmd.personalization.prepare_catalog_item")
    async def prepare_catalog_item(self, env, cmd: PrepareCatalogItem, conn):
        row = await self._product(conn, cmd.sku)
        reason = None
        if row is None:
            reason = f"SKU {cmd.sku} is not in the catalog"
        elif not row["active"]:
            reason = f"SKU {cmd.sku} is switched off in the catalog"
        elif not local_path(row["file_uri"]).is_file():
            reason = f"the template of {cmd.sku} is missing on the artifacts volume: import it again"
        if reason:
            await self.emit(conn, ModelFailed(order_id=cmd.order_id, item_id=cmd.item_id, reason=reason), cause=env)
            return
        await self.emit(conn, ModelReady(
            order_id=cmd.order_id, item_id=cmd.item_id, artifact_uri=row["file_uri"], source_uri=row["file_uri"],
            footprint_mm=(row["footprint_w"], row["footprint_d"]), est_grams=row["est_grams"],
            est_minutes=row["est_minutes"], sliced=row["sliced"],
        ), cause=env)
