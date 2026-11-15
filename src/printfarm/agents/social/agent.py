"""Social Listening agent (ADR-0007: captured by people, replies approved and posted by people).

A capture (``cmd.social.ingest_capture``, from the browser extension via the gateway) goes through:

1. duplicate check (normalised text) - the same post captured twice is one capture;
2. a local embedding, compared with labelled posts (``social.examples``: seeds + every capture a person labels):
   far from every buyer post -> "ignored" without a model call (the cheap gate, ``social.prefilter_min``);
3. claude-haiku-5-5 triage (intent, 0-1 score, the best of the 5 nearest SKUs, a summary);
4. a buying intent at or above ``social.intent_threshold`` becomes a lead, and claude-opus-5-5 drafts the Hebrew
   reply. The lead waits on the Social screen: a person edits, approves ("Approve & Copy"), posts it themselves,
   and marks it posted. Nothing is ever posted, sent or scraped by this agent.

Model calls run after the capture is stored (never inside a handler transaction) and are recorded in
``social.llm_calls``; at the daily budget, or without ``ANTHROPIC_API_KEY``, captures wait and can be rescored.
"""
from __future__ import annotations

import asyncio
import uuid

from printfarm.agents.base import Agent, on
from printfarm.agents.social.embed import Embedder, make_embedder, to_pg
from printfarm.agents.social.llm import Business, Candidate, LLMError, SocialLLM, Usage, make_llm
from printfarm.contracts.messages import (
    CaptureLabelled,
    CaptureScored,
    CaptureSubmitted,
    CatalogProductUpdated,
    DecideLead,
    LabelCapture,
    LeadChanged,
    LeadDetected,
    MarkLeadPosted,
    ReplyDrafted,
    RescoreCapture,
)
from printfarm.contracts.social import clean_post, prefilter_score, text_hash

LEAD_INTENTS = {"buy", "recommend"}
DAY = "date_trunc('day', now() AT TIME ZONE 'Asia/Jerusalem')"


class SocialAgent(Agent):
    name = "social"

    def __init__(self, *a, embedder: Embedder | None = None, llm: SocialLLM | None | bool = False,
                 business: Business | None = None, **kw):
        super().__init__(*a, **kw)
        self.embedder = embedder
        self.llm = make_llm() if llm is False else llm       # False: from the environment; None: no model
        self.business = business or Business.load()
        self._busy: set[str] = set()

    async def on_start(self) -> None:
        if self.embedder is None:
            self.embedder = await asyncio.to_thread(make_embedder)
        self.log.info("embeddings: %s; model calls: %s", self.embedder.name,
                      type(self.llm).__name__ if self.llm else "none (set ANTHROPIC_API_KEY)")
        await self._embed_missing_examples()
        await self._backfill_skus()
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT capture_id FROM social.captures WHERE status IN ('new', 'scoring') ORDER BY received_at")).fetchall()
        for r in rows:                                          # interrupted by a restart
            self.spawn(self._process(r["capture_id"]))

    def heartbeat_info(self) -> dict:
        llm = {"ClaudeLLM": "claude", "FakeLLM": "fake"}.get(type(self.llm).__name__, "none") if self.llm else "none"
        return {"llm": llm, "embedder": getattr(self.embedder, "name", None)}

    # ------------------------------------------------------------ commands

    @on("cmd.social.ingest_capture")
    async def ingest(self, env, cmd: CaptureSubmitted, conn):
        text = clean_post(cmd.post_text)
        digest = text_hash(text)
        dup = await (await conn.execute(
            "SELECT capture_id FROM social.captures WHERE text_hash = %s", (digest,))).fetchone()
        if dup:
            await self.emit(conn, CaptureScored(capture_id=cmd.capture_id, group_name=cmd.group_name, status="duplicate",
                                                reason=f"same post as {dup['capture_id']}"), cause=env)
            return
        await conn.execute(
            "INSERT INTO social.captures (capture_id, group_name, post_url, post_text, text_hash, captured_by, captured_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (capture_id) DO NOTHING",
            (cmd.capture_id, cmd.group_name.strip()[:200], cmd.post_url.strip()[:1000], text, digest,
             cmd.captured_by[:200], cmd.captured_at))
        self.after_commit(lambda: self.spawn(self._process(cmd.capture_id)))

    @on("cmd.social.rescore")
    async def rescore(self, env, cmd: RescoreCapture, conn):
        row = await (await conn.execute(
            "UPDATE social.captures SET status = 'new', status_reason = '' WHERE capture_id = %s "
            "AND status IN ('waiting', 'failed', 'ignored', 'scored') RETURNING capture_id", (cmd.capture_id,))).fetchone()
        if row:
            self.after_commit(lambda: self.spawn(self._process(cmd.capture_id)))
        else:
            self.log.warning("rescore of %s refused: unknown, already a lead, or being scored", cmd.capture_id)

    @on("cmd.social.decide_lead")
    async def decide(self, env, cmd: DecideLead, conn):
        lead = await (await conn.execute(
            "SELECT * FROM social.leads WHERE lead_id = %s FOR UPDATE", (cmd.lead_id,))).fetchone()
        if not lead or lead["status"] not in ("pending", "approved"):
            self.log.warning("decision on lead %s refused (status %s)", cmd.lead_id, lead and lead["status"])
            return
        if cmd.approve:
            text = (cmd.final_text or lead["draft"]).strip()
            if not text:
                self.log.warning("approval of lead %s refused: no reply text", cmd.lead_id)
                return
            status = "approved"
        else:
            text, status = "", "dismissed"
        await conn.execute(
            "UPDATE social.leads SET status = %s, final_text = %s, decided_by = %s, decided_at = now() WHERE lead_id = %s",
            (status, text or None, cmd.decided_by, cmd.lead_id))
        await self.emit(conn, LeadChanged(lead_id=cmd.lead_id, capture_id=lead["capture_id"], status=status,
                                          by=cmd.decided_by, final_text=text), cause=env)

    @on("cmd.social.mark_posted")
    async def posted(self, env, cmd: MarkLeadPosted, conn):
        lead = await (await conn.execute(
            "UPDATE social.leads SET status = 'posted', posted_by = %s, posted_at = now() "
            "WHERE lead_id = %s AND status = 'approved' RETURNING capture_id, final_text",
            (cmd.posted_by, cmd.lead_id))).fetchone()
        if not lead:
            self.log.warning("mark-posted of lead %s refused: approve it first", cmd.lead_id)
            return
        await self.emit(conn, LeadChanged(lead_id=cmd.lead_id, capture_id=lead["capture_id"], status="posted",
                                          by=cmd.posted_by, final_text=lead["final_text"] or ""), cause=env)

    @on("cmd.social.label_capture")
    async def label(self, env, cmd: LabelCapture, conn):
        cap = await (await conn.execute(
            "UPDATE social.captures SET label = %s, labelled_by = %s WHERE capture_id = %s "
            "RETURNING post_text, embedding IS NOT NULL AS embedded", (cmd.label, cmd.labelled_by, cmd.capture_id))).fetchone()
        if not cap:
            self.log.warning("label for unknown capture %s", cmd.capture_id)
            return
        # The labelled capture teaches the prefilter (its embedding is copied; re-labelling replaces the verdict).
        await conn.execute(
            "INSERT INTO social.examples (example_id, text, label, source, embedding) "
            "SELECT %s, post_text, %s, 'capture', embedding FROM social.captures WHERE capture_id = %s "
            "ON CONFLICT (example_id) DO UPDATE SET label = EXCLUDED.label, embedding = EXCLUDED.embedding",
            (f"cap-{cmd.capture_id}", cmd.label, cmd.capture_id))
        await self.emit(conn, CaptureLabelled(capture_id=cmd.capture_id, label=cmd.label, by=cmd.labelled_by), cause=env)

    @on("evt.catalog.product.updated", from_start=True)
    async def product_updated(self, env, ev: CatalogProductUpdated, conn):
        row = await (await conn.execute(
            "SELECT sku, name, category, price_agorot, active FROM catalog.products WHERE sku = %s", (ev.sku,))).fetchone()
        if row:
            await self._upsert_sku(conn, row)

    # ------------------------------------------------------------ the pipeline

    async def _process(self, capture_id: str) -> None:
        if capture_id in self._busy:
            return
        self._busy.add(capture_id)
        try:
            await self._score(capture_id)
        except Exception as exc:  # noqa: BLE001 - one bad post must not stop the others; it can be rescored
            self.log.exception("scoring %s failed", capture_id)
            await self._finish(capture_id, "failed", f"{type(exc).__name__}: {exc}"[:300])
        finally:
            self._busy.discard(capture_id)

    async def _score(self, capture_id: str) -> None:
        async with self.db.tx() as conn:
            cap = await (await conn.execute(
                "UPDATE social.captures SET status = 'scoring' WHERE capture_id = %s AND status IN ('new', 'scoring') "
                "RETURNING *", (capture_id,))).fetchone()
        if not cap:
            return
        if not self.setting("social.enabled"):
            return await self._finish(capture_id, "waiting", "social listening is switched off (System > settings)")

        [vec] = await self.embedder.embed([cap["post_text"]])
        q = to_pg(vec)
        async with self.db.tx() as conn:
            await conn.execute("UPDATE social.captures SET embedding = %s::vector WHERE capture_id = %s", (q, capture_id))
            near = await (await conn.execute(
                "SELECT label, 1 - (embedding <=> %s::vector) AS sim FROM social.examples "
                "WHERE embedding IS NOT NULL AND example_id <> %s ORDER BY embedding <=> %s::vector LIMIT 5",
                (q, f"cap-{capture_id}", q))).fetchall()
            sku_rows = await (await conn.execute(
                "SELECT sku, name, category, price_agorot, 1 - (embedding <=> %s::vector) AS sim FROM social.sku_vectors "
                "WHERE active ORDER BY embedding <=> %s::vector LIMIT 5", (q, q))).fetchall()
        pre = prefilter_score([(r["label"], float(r["sim"])) for r in near])
        minimum = float(self.setting("social.prefilter_min"))
        if pre < minimum:
            return await self._finish(capture_id, "ignored", f"prefilter {pre:.2f} < {minimum:.2f}: not like a buyer post",
                                      prefilter=pre)
        if self.llm is None:
            return await self._finish(capture_id, "waiting", "no ANTHROPIC_API_KEY: set it in .env, then Rescore",
                                      prefilter=pre)
        if await self._over_budget():
            return await self._finish(capture_id, "waiting", "today's model budget is used up: Rescore tomorrow",
                                      prefilter=pre)

        cands = [Candidate(r["sku"], r["name"], r["category"], r["price_agorot"], float(r["sim"])) for r in sku_rows]
        try:
            intent, usage = await self.llm.classify(cap["group_name"], cap["post_text"], cands)
        except LLMError as exc:
            await self._record("classify", capture_id, exc.usage, "refused" if exc.refused else f"error: {exc}")
            return await self._finish(capture_id, "failed", str(exc)[:300], prefilter=pre)
        await self._record("classify", capture_id, usage, "ok")

        threshold = float(self.setting("social.intent_threshold"))
        is_lead = intent.intent in LEAD_INTENTS and intent.score >= threshold
        lead_id = f"lead-{uuid.uuid4().hex[:12]}" if is_lead else None
        if is_lead:
            reason = ""
        elif intent.intent in LEAD_INTENTS:
            reason = f"{intent.intent} {intent.score:.2f} < threshold {threshold:.2f}"
        else:
            reason = f"not a request ({intent.intent})"
        async with self.db.tx() as conn:
            await conn.execute(
                "UPDATE social.captures SET status = %s, status_reason = %s, prefilter = %s, intent = %s, intent_score = %s, "
                "sku_guess = %s, summary = %s, scored_at = now() WHERE capture_id = %s",
                ("lead" if is_lead else "scored", reason, pre, intent.intent, intent.score, intent.sku,
                 intent.summary, capture_id))
            await self.emit(conn, CaptureScored(
                capture_id=capture_id, group_name=cap["group_name"], status="lead" if is_lead else "scored", prefilter=pre,
                intent=intent.intent, intent_score=intent.score, sku_guess=intent.sku, summary=intent.summary))
            if is_lead:
                await conn.execute(
                    "INSERT INTO social.leads (lead_id, capture_id, intent_score, sku, summary) VALUES (%s, %s, %s, %s, %s)",
                    (lead_id, capture_id, intent.score, intent.sku, intent.summary))
                await self.emit(conn, LeadDetected(lead_id=lead_id, capture_id=capture_id, intent_score=intent.score,
                                                   product_guess=intent.sku or "", summary=intent.summary))
        self.relay.wakeup.set()
        if is_lead:
            await self._draft(lead_id, cap, intent, cands)

    async def _draft(self, lead_id: str, cap: dict, intent, cands: list[Candidate]) -> None:
        draft, notes, failed = "", "", ""
        if await self._over_budget():
            failed = "today's model budget is used up: write the reply yourself"
        else:
            try:
                out, usage = await self.llm.draft(cap["group_name"], cap["post_text"], intent, cands, self.business)
                await self._record("draft", cap["capture_id"], usage, "ok")
                draft, notes = out.reply, out.notes
            except LLMError as exc:
                await self._record("draft", cap["capture_id"], exc.usage, "refused" if exc.refused else f"error: {exc}")
                failed = str(exc)[:300]
        async with self.db.tx() as conn:
            # The lead is shown either way: without a draft, the person writes the reply.
            await conn.execute(
                "UPDATE social.leads SET status = 'pending', draft = %s, draft_notes = %s, status_reason = %s "
                "WHERE lead_id = %s AND status = 'drafting'", (draft, notes, failed, lead_id))
            await self.emit(conn, ReplyDrafted(lead_id=lead_id, capture_id=cap["capture_id"], draft=draft,
                                               sku=intent.sku, failed=failed))
        self.relay.wakeup.set()

    # ------------------------------------------------------------ helpers

    async def _finish(self, capture_id: str, status: str, reason: str, prefilter: float | None = None) -> None:
        async with self.db.tx() as conn:
            cap = await (await conn.execute(
                "UPDATE social.captures SET status = %s, status_reason = %s, prefilter = COALESCE(%s, prefilter), "
                "scored_at = now() WHERE capture_id = %s RETURNING group_name",
                (status, reason, prefilter, capture_id))).fetchone()
            if cap:
                await self.emit(conn, CaptureScored(capture_id=capture_id, group_name=cap["group_name"], status=status,
                                                    reason=reason, prefilter=prefilter))
        self.relay.wakeup.set()

    async def _record(self, purpose: str, capture_id: str, usage: Usage | None, outcome: str) -> None:
        if usage is None:
            return
        async with self.db.tx() as conn:
            await conn.execute(
                "INSERT INTO social.llm_calls (purpose, model, capture_id, input_tokens, output_tokens, cache_read_tokens, "
                "cache_write_tokens, cost_usd, outcome) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (purpose, usage.model, capture_id, usage.input_tokens, usage.output_tokens, usage.cache_read,
                 usage.cache_write, usage.cost, outcome[:300]))

    async def _over_budget(self) -> bool:
        budget = float(self.setting("social.daily_budget_usd"))
        async with self.db.tx() as conn:
            spent = await (await conn.execute(
                f"SELECT COALESCE(sum(cost_usd), 0) AS s FROM social.llm_calls WHERE at AT TIME ZONE 'Asia/Jerusalem' >= {DAY}"
            )).fetchone()
        return float(spent["s"]) >= budget

    async def _embed_missing_examples(self) -> None:
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT example_id, text FROM social.examples WHERE embedding IS NULL")).fetchall()
        if not rows:
            return
        vectors = await self.embedder.embed([r["text"] for r in rows])
        async with self.db.tx() as conn:
            for r, v in zip(rows, vectors, strict=True):
                await conn.execute("UPDATE social.examples SET embedding = %s::vector WHERE example_id = %s",
                                   (to_pg(v), r["example_id"]))
        self.log.info("embedded %d labelled examples", len(rows))

    async def _backfill_skus(self) -> None:
        """Catalog SKUs without a vector (first start, or SKUs saved while this agent was down)."""
        async with self.db.tx() as conn:
            rows = await (await conn.execute(
                "SELECT p.sku, p.name, p.category, p.price_agorot, p.active FROM catalog.products p "
                "LEFT JOIN social.sku_vectors v USING (sku) WHERE v.sku IS NULL OR v.updated_at < p.updated_at")).fetchall()
            for r in rows:
                await self._upsert_sku(conn, r)

    async def _upsert_sku(self, conn, row: dict) -> None:
        [v] = await self.embedder.embed([f"{row['name']} ({row['category']})"])
        await conn.execute(
            "INSERT INTO social.sku_vectors (sku, name, category, price_agorot, active, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s::vector) ON CONFLICT (sku) DO UPDATE SET name = EXCLUDED.name, "
            "category = EXCLUDED.category, price_agorot = EXCLUDED.price_agorot, active = EXCLUDED.active, "
            "embedding = EXCLUDED.embedding, updated_at = now()",
            (row["sku"], row["name"], row["category"], row["price_agorot"], row["active"], to_pg(v)))
