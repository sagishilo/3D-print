"""Personalization Agent: turns an order line into a printable artifact, headlessly (one per order line).

* a nameplate: its text -> OpenSCAD model (nameplate.py);
* a catalog SKU: its 3MF template from the Instant Catalog (catalog.products, owned here; catalog.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

from psycopg.types.json import Jsonb

from printfarm.agents.base import Agent, on
from printfarm.agents.personalization.catalog import InvalidThreeMf, ThreeMfInfo, inspect_3mf, thumbnail_3mf
from printfarm.agents.personalization.nameplate import InvalidText, NameplateSpec, generate, safe_name
from printfarm.agents.personalization.slicer import SliceError, find_slicer, slice_3mf
from printfarm.agents.telemetry.platefiles import local_path
from printfarm.contracts.catalog import (
    MAX_IMAGE_BYTES,
    REFERENCES_SQL,
    UNASSIGNED,
    UPLOAD_BUCKET,
    delete_blocker,
    image_type,
)
from printfarm.contracts.messages import (
    ArchiveNiche,
    AssignNiche,
    CatalogProductDeleted,
    CatalogProductRejected,
    CatalogProductUpdated,
    DeleteCatalogProduct,
    GenerateNameplate,
    ModelFailed,
    ModelReady,
    NicheRejected,
    NicheUpdated,
    PrepareCatalogItem,
    SetCatalogProductActive,
    SliceCatalogProduct,
    UpsertCatalogProduct,
    UpsertNiche,
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

    async def _image(self, conn, sku: str):
        return await (await conn.execute("SELECT sha256, source FROM catalog.product_images WHERE sku = %s",
                                         (sku,))).fetchone()

    async def _save_image(self, conn, sku: str, pic: bytes, mime: str, source: str, by: str) -> None:
        await conn.execute(
            "INSERT INTO catalog.product_images (sku, sha256, mime, data, source, updated_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (sku) DO UPDATE SET sha256 = EXCLUDED.sha256, "
            "mime = EXCLUDED.mime, data = EXCLUDED.data, source = EXCLUDED.source, updated_by = EXCLUDED.updated_by, "
            "updated_at = now()",
            (sku, hashlib.sha256(pic).hexdigest(), mime, pic, source, by))

    async def _uploaded_image(self, sha: str) -> tuple[bytes, str] | str:
        """The picture uploaded for a product, checked again here: (bytes, type), or why it cannot be used."""
        pic = await self.bus.blob_get(UPLOAD_BUCKET, sha)
        if pic is None:
            return "the picture upload is gone (kept 7 days): upload it again"
        if hashlib.sha256(pic).hexdigest() != sha:
            return "the picture does not match its checksum"
        if len(pic) > MAX_IMAGE_BYTES:
            return f"a picture may be at most {MAX_IMAGE_BYTES // (1024 * 1024)} MB"
        kind = image_type(pic)
        if kind is None:
            return "the picture is not a PNG, JPEG or WebP image"
        return pic, kind

    @on("cmd.personalization.upsert_product")
    async def upsert_product(self, env, cmd: UpsertCatalogProduct, conn):
        by = cmd.changed_by or "unknown"
        row = await self._product(conn, cmd.sku)
        file: dict = {}
        info: ThreeMfInfo | None = None
        data: bytes | None = None
        picture: tuple[bytes, str] | None = None
        if cmd.image_upload:
            got = await self._uploaded_image(cmd.image_upload)
            if isinstance(got, str):
                return await self._reject(conn, env, cmd.sku, "upsert", got, by)
            picture = got
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
                    "footprint_d": info.footprint_mm[1], "filament_count": max(len(info.filaments), 1),
                    "max_z_mm": info.max_z_mm}
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

        niches = await self._niches(conn)
        wanted = [n for n in [cmd.niche, *(cmd.tags or [])] if n]
        unknown = sorted({n for n in wanted if n not in niches})
        if unknown:
            return await self._reject(conn, env, cmd.sku, "upsert", f"no niche {', '.join(unknown)}", by)
        archived = sorted({n for n in wanted if niches[n]} - ({row["niche"]} if row else set()))
        if archived:
            return await self._reject(conn, env, cmd.sku, "upsert", f"niche {', '.join(archived)} is archived", by)

        values = {"name": cmd.name, "category": cmd.category, "material": cmd.material, "color": cmd.color,
                  "price_agorot": cmd.price_agorot, "est_grams": grams, "est_minutes": minutes, "estimated": estimated,
                  "active": cmd.active, "updated_by": by, **file}
        if cmd.niche is not None:
            values["niche"] = cmd.niche
        if cmd.post_minutes is not None:
            values["post_minutes"] = cmd.post_minutes
        if row is None:
            cols = [*values, "created_by"]
            await conn.execute(
                f"INSERT INTO catalog.products (sku, {', '.join(cols)}) VALUES (%s{', %s' * len(cols)})",
                (cmd.sku, *values.values(), by))
        else:
            sets = ", ".join(f"{k} = %s" for k in values)
            await conn.execute(f"UPDATE catalog.products SET {sets}, updated_at = now() WHERE sku = %s",
                               (*values.values(), cmd.sku))
        if cmd.tags is not None:
            main = values.get("niche", row["niche"] if row else UNASSIGNED)
            await conn.execute("DELETE FROM catalog.product_tags WHERE sku = %s", (cmd.sku,))
            for tag in sorted(set(cmd.tags) - {main}):
                await conn.execute("INSERT INTO catalog.product_tags (sku, niche) VALUES (%s, %s)", (cmd.sku, tag))
        await self._update_image(conn, cmd.sku, row, picture, data, by)
        sliced = file.get("sliced", row["sliced"] if row else False)
        self.log.info("catalog %s %s (%s)", "added" if row is None else "updated", cmd.sku,
                      "sliced" if sliced else "unsliced")
        await self.emit(conn, CatalogProductUpdated(sku=cmd.sku, name=cmd.name, active=cmd.active, sliced=sliced, by=by),
                        cause=env)

    async def _update_image(self, conn, sku: str, row, picture: tuple[bytes, str] | None, data: bytes | None,
                            by: str) -> None:
        """An uploaded picture wins. Otherwise the 3MF's own preview: from a new template (unless the operator uploaded
        a picture), or, for a product that has none yet, from its stored template (products imported before images)."""
        if picture:
            await self._save_image(conn, sku, *picture, "upload", by)
            return
        current = await self._image(conn, sku)
        if data is None:
            if current is not None or row is None:
                return
            path = local_path(row["file_uri"])
            if not path.is_file():
                return
            data = await asyncio.to_thread(path.read_bytes)
        elif current is not None and current["source"] == "upload":
            return
        thumb = await asyncio.to_thread(thumbnail_3mf, data)
        if thumb:
            await self._save_image(conn, sku, *thumb, "3mf", by)

    @on("cmd.personalization.set_product_active")
    async def set_product_active(self, env, cmd: SetCatalogProductActive, conn):
        by = cmd.changed_by or "unknown"
        row = await (await conn.execute(
            "UPDATE catalog.products SET active = %s, updated_by = %s, updated_at = now() WHERE sku = %s "
            "RETURNING name, sliced", (cmd.active, by, cmd.sku))).fetchone()
        if row is None:
            return await self._reject(conn, env, cmd.sku, "active", f"no SKU {cmd.sku} in the catalog", by)
        self.log.info("catalog %s switched %s by %s", cmd.sku, "on" if cmd.active else "off", by)
        await self.emit(conn, CatalogProductUpdated(sku=cmd.sku, name=row["name"], active=cmd.active,
                                                    sliced=row["sliced"], by=by), cause=env)

    @on("cmd.personalization.delete_product")
    async def delete_product(self, env, cmd: DeleteCatalogProduct, conn):
        by = cmd.changed_by or "unknown"
        row = await self._product(conn, cmd.sku)
        if row is None:
            return await self._reject(conn, env, cmd.sku, "delete", f"no SKU {cmd.sku} in the catalog", by)
        # A read-only look at what still points at the SKU (orders, the shelf, store listings): those keep it.
        refs = await (await conn.execute(REFERENCES_SQL, {"sku": cmd.sku})).fetchone()
        blocked = delete_blocker(refs)
        if blocked:
            return await self._reject(conn, env, cmd.sku, "delete", f"not deleted: {blocked}", by)
        await conn.execute("DELETE FROM catalog.products WHERE sku = %s", (cmd.sku,))   # tags and image cascade
        await self.emit(conn, CatalogProductDeleted(sku=cmd.sku, name=row["name"], by=by), cause=env)
        removed = await asyncio.to_thread(self._remove_files, cmd.sku)
        self.log.info("catalog %s deleted by %s (%d template files removed)", cmd.sku, by, removed)

    def _remove_files(self, sku: str) -> int:
        """This SKU's templates on the artifacts volume (``<SKU>-<12 hex>.3mf`` and ``.sliced.3mf``), nothing else."""
        mine = re.compile(rf"^{re.escape(sku)}-[0-9a-f]{{12}}(\.sliced)?\.3mf$")
        folder = self._catalog_dir()
        n = 0
        for f in folder.iterdir() if folder.is_dir() else ():
            if mine.match(f.name):
                f.unlink(missing_ok=True)
                n += 1
        return n

    # ------------------------------------------------------------ niches (catalog.niches, owned here)

    async def _niches(self, conn) -> dict[str, bool]:
        """Every niche slug -> archived."""
        rows = await (await conn.execute("SELECT slug, archived FROM catalog.niches")).fetchall()
        return {r["slug"]: r["archived"] for r in rows}

    async def _niche_rejected(self, conn, env, slug: str, reason: str, by: str) -> None:
        self.log.info("niche %s refused: %s", slug, reason)
        await self.emit(conn, NicheRejected(slug=slug, reason=reason, by=by or "unknown"), cause=env)

    @on("cmd.personalization.upsert_niche")
    async def upsert_niche(self, env, cmd: UpsertNiche, conn):
        by = cmd.changed_by or "unknown"
        clean = lambda xs: [x.strip() for x in xs if x.strip()]  # noqa: E731
        values = {"name": cmd.name.strip(), "color": cmd.color, "icon": cmd.icon, "sort": cmd.sort,
                  "default_material": cmd.default_material, "default_colors": Jsonb(clean(cmd.default_colors)),
                  "target_margin_pct": cmd.target_margin_pct, "qc_checklist": Jsonb(clean(cmd.qc_checklist)),
                  "packaging": Jsonb(clean(cmd.packaging)), "updated_by": by}
        cols = ", ".join(values)
        sets = ", ".join(f"{k} = EXCLUDED.{k}" for k in values)
        row = await (await conn.execute(
            f"INSERT INTO catalog.niches (slug, {cols}) VALUES (%s{', %s' * len(values)}) "
            f"ON CONFLICT (slug) DO UPDATE SET {sets}, updated_at = now() RETURNING archived",
            (cmd.slug, *values.values()))).fetchone()
        self.log.info("niche %s saved by %s", cmd.slug, by)
        await self.emit(conn, NicheUpdated(slug=cmd.slug, name=values["name"], archived=row["archived"], by=by), cause=env)

    @on("cmd.personalization.archive_niche")
    async def archive_niche(self, env, cmd: ArchiveNiche, conn):
        by = cmd.changed_by or "unknown"
        niches = await self._niches(conn)
        if cmd.slug not in niches:
            return await self._niche_rejected(conn, env, cmd.slug, f"no niche {cmd.slug}", by)
        if cmd.slug == UNASSIGNED:
            return await self._niche_rejected(conn, env, cmd.slug, "Unassigned is where products without a niche go: "
                                              "it cannot be archived", by)
        moved = 0
        if cmd.archived:
            if cmd.move_to == cmd.slug or niches.get(cmd.move_to, True):
                return await self._niche_rejected(conn, env, cmd.slug, f"move its products to an active niche, not "
                                                  f"{cmd.move_to}", by)
            # Products move on; order lines keep the niche they were sold under (history is not rewritten).
            moved = (await conn.execute("UPDATE catalog.products SET niche = %s, updated_by = %s, updated_at = now() "
                                        "WHERE niche = %s", (cmd.move_to, by, cmd.slug))).rowcount
            await conn.execute("DELETE FROM catalog.product_tags WHERE niche = %s", (cmd.slug,))
        row = await (await conn.execute(
            "UPDATE catalog.niches SET archived = %s, updated_by = %s, updated_at = now() WHERE slug = %s RETURNING name",
            (cmd.archived, by, cmd.slug))).fetchone()
        self.log.info("niche %s %s by %s (%d products moved)", cmd.slug, "archived" if cmd.archived else "restored", by,
                      moved)
        await self.emit(conn, NicheUpdated(slug=cmd.slug, name=row["name"], archived=cmd.archived, by=by,
                                           skus_moved=moved), cause=env)

    @on("cmd.personalization.assign_niche")
    async def assign_niche(self, env, cmd: AssignNiche, conn):
        by = cmd.changed_by or "unknown"
        niches = await self._niches(conn)
        if cmd.niche not in niches or niches[cmd.niche]:
            return await self._niche_rejected(conn, env, cmd.niche, f"no active niche {cmd.niche}", by)
        moved = (await conn.execute(
            "UPDATE catalog.products SET niche = %s, updated_by = %s, updated_at = now() WHERE sku = ANY(%s)",
            (cmd.niche, by, list(cmd.skus)))).rowcount
        await conn.execute("DELETE FROM catalog.product_tags WHERE niche = %s AND sku = ANY(%s)", (cmd.niche, list(cmd.skus)))
        row = await (await conn.execute("SELECT name FROM catalog.niches WHERE slug = %s", (cmd.niche,))).fetchone()
        self.log.info("%d products moved to niche %s by %s", moved, cmd.niche, by)
        await self.emit(conn, NicheUpdated(slug=cmd.niche, name=row["name"], archived=False, by=by, skus_moved=moved),
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
            "est_minutes = %s, estimated = %s, filament_count = %s, max_z_mm = %s, updated_by = %s, updated_at = now() "
            "WHERE sku = %s",
            (out.resolve().as_uri(), info.printer_model, grams, minutes, not (info.est_grams and info.est_minutes),
             max(len(info.filaments), 1), info.max_z_mm, by, cmd.sku))
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
            est_minutes=row["est_minutes"], sliced=row["sliced"], printer_model=row["printer_model"],
            filament_count=row["filament_count"], max_z_mm=row["max_z_mm"],
        ), cause=env)
