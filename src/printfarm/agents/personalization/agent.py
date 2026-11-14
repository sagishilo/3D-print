"""Personalization Agent: turns an order line's text into a printable artifact, headlessly (one per order line)."""

from __future__ import annotations

from pathlib import Path

from printfarm.agents.base import Agent, on
from printfarm.agents.personalization.nameplate import InvalidText, NameplateSpec, generate, safe_name
from printfarm.contracts.messages import GenerateNameplate, ModelFailed, ModelReady


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
