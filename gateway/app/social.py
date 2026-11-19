"""Social Listening read side and capture tokens (ADR-0007).

- Capture tokens: one per browser or device that runs the capture extension. Created here (the token is shown
  once; only its SHA-256 is kept), listed, revoked. ``verify_token`` is used by ``POST /api/v1/social/captures``
  in main.py, the only route a token (not the login cookie) can call.
- Leads (with the post, the draft and what happened to it), recent captures (with why each was ignored, waited
  or failed), and the screen's numbers: captures, leads, posted replies, model spend, and how well the scores
  agree with the posts a person labelled.

Changes go through commands (social.decide / posted / label / rescore / capture).
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from printfarm.contracts.social import CAPTURE_STATUSES, CLASSIFY_MODEL, DRAFT_MODEL

from . import auth

router = APIRouter(prefix="/api/v1/social", dependencies=[Depends(auth.require_auth)])
TOKEN_PREFIX = "pfc_"
DAY = "date_trunc('day', now() AT TIME ZONE 'Asia/Jerusalem')"
OPEN_LEADS = ("drafting", "pending", "approved")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def verify_token(db, authorization: str | None) -> dict:
    """The capture token from ``Authorization: Bearer pfc_...``; 401 if missing, unknown or revoked."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "capture token missing (Authorization: Bearer ...)")
    token = authorization[7:].strip()
    if not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        raise HTTPException(401, "not a capture token")
    row = await db.fetchrow(
        "UPDATE gateway.capture_tokens SET last_used_at = now(), uses = uses + 1 "
        "WHERE token_hash = %s AND revoked_at IS NULL RETURNING token_id, label", (token_hash(token),))
    if not row:
        raise HTTPException(401, "capture token unknown or revoked")
    return row


# ---------------------------------------------------------------- tokens


class TokenIn(BaseModel):
    label: str = Field(min_length=1, max_length=60)


@router.get("/tokens")
async def tokens(request: Request):
    rows = await request.app.state.db.fetchall(
        "SELECT token_id, label, created_by, created_at, last_used_at, uses, revoked_at FROM gateway.capture_tokens "
        "ORDER BY revoked_at IS NOT NULL, created_at DESC")
    return {"tokens": rows}


@router.post("/tokens", status_code=201)
async def create_token(body: TokenIn, request: Request, claims: dict = Depends(auth.OWNER_CHANGE)):
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_id = f"tok-{secrets.token_hex(4)}"
    db = request.app.state.db
    await db.execute(
        "INSERT INTO gateway.capture_tokens (token_id, token_hash, label, created_by) VALUES (%s, %s, %s, %s)",
        (token_id, token_hash(token), body.label.strip(), claims["sub"]))
    await auth._audit(request, "social.token.created", claims["sub"], "done", token_id=token_id, label=body.label.strip())
    return {"token_id": token_id, "label": body.label.strip(), "token": token}   # shown once


@router.post("/tokens/{token_id}/revoke")
async def revoke_token(token_id: str, request: Request, claims: dict = Depends(auth.OWNER_CHANGE)):
    n = await request.app.state.db.execute(
        "UPDATE gateway.capture_tokens SET revoked_at = now() WHERE token_id = %s AND revoked_at IS NULL", (token_id,))
    if not n:
        raise HTTPException(404, "no such active token")
    await auth._audit(request, "social.token.revoked", claims["sub"], "done", token_id=token_id)
    return {"revoked": True}


# ---------------------------------------------------------------- leads and captures

LEAD_SQL = (
    "SELECT l.*, c.group_name, c.post_url, c.post_text, c.captured_by, c.captured_at, c.intent, c.prefilter, "
    "s.name AS sku_name, s.price_agorot AS sku_price_agorot "
    "FROM social.leads l JOIN social.captures c USING (capture_id) LEFT JOIN social.sku_vectors s ON s.sku = l.sku ")


@router.get("/leads")
async def leads(request: Request, status: str = Query("open", pattern="^(open|posted|dismissed|all)$"),
                limit: int = Query(100, ge=1, le=500)):
    where = {"open": "WHERE l.status = ANY(%s) ", "posted": "WHERE l.status = 'posted' ",
             "dismissed": "WHERE l.status = 'dismissed' ", "all": ""}[status]
    params: tuple = (list(OPEN_LEADS), limit) if status == "open" else (limit,)
    rows = await request.app.state.db.fetchall(LEAD_SQL + where + "ORDER BY l.created_at DESC LIMIT %s", params)
    return {"leads": rows}


@router.get("/captures")
async def captures(request: Request, status: str | None = Query(None), limit: int = Query(100, ge=1, le=500)):
    if status is not None and status not in CAPTURE_STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(CAPTURE_STATUSES)}")
    rows = await request.app.state.db.fetchall(
        "SELECT capture_id, group_name, post_url, left(post_text, 600) AS post_text, captured_by, captured_at, "
        "received_at, status, status_reason, prefilter, intent, intent_score, sku_guess, summary, label, labelled_by "
        "FROM social.captures " + ("WHERE status = %s " if status else "") + "ORDER BY received_at DESC LIMIT %s",
        (status, limit) if status else (limit,))
    return {"captures": rows}


def accuracy(rows: list[dict]) -> dict:
    """How the model's verdict (the capture became a lead) agrees with a person's label (pure).
    Precision: of the leads, how many were real buyers; recall: of the real buyers, how many became leads."""
    tp = sum(1 for r in rows if r["label"] == "buyer" and r["lead"])
    fp = sum(1 for r in rows if r["label"] == "not_buyer" and r["lead"])
    fn = sum(1 for r in rows if r["label"] == "buyer" and not r["lead"])
    tn = sum(1 for r in rows if r["label"] == "not_buyer" and not r["lead"])
    n = tp + fp + fn + tn
    return {"labelled": n, "true_leads": tp, "false_leads": fp, "missed": fn, "correctly_ignored": tn,
            "precision": round(tp / (tp + fp), 3) if tp + fp else None,
            "recall": round(tp / (tp + fn), 3) if tp + fn else None,
            "agreement": round((tp + tn) / n, 3) if n else None}


@router.get("/stats")
async def stats(request: Request):
    db, bus = request.app.state.db, request.app.state.bus
    counts = await db.fetchall(
        f"SELECT status, count(*) AS n, count(*) FILTER (WHERE received_at AT TIME ZONE 'Asia/Jerusalem' >= {DAY}) AS today "
        "FROM social.captures WHERE received_at > now() - interval '7 days' GROUP BY status")
    groups = await db.fetchrow(
        "SELECT count(DISTINCT group_name) FILTER (WHERE group_name <> '') AS groups, max(received_at) AS last_capture "
        "FROM social.captures WHERE received_at > now() - interval '30 days'")
    leads_by = await db.fetchall("SELECT status, count(*) AS n FROM social.leads GROUP BY status")
    posted_7d = await db.fetchval(
        "SELECT count(*) FROM social.leads WHERE status = 'posted' AND posted_at > now() - interval '7 days'")
    spend = await db.fetchall(
        f"SELECT model, count(*) AS calls, COALESCE(sum(cost_usd), 0)::float AS usd, "
        f"COALESCE(sum(cost_usd) FILTER (WHERE at AT TIME ZONE 'Asia/Jerusalem' >= {DAY}), 0)::float AS usd_today, "
        "count(*) FILTER (WHERE outcome <> 'ok') AS problems "
        "FROM social.llm_calls WHERE at > date_trunc('month', now()) GROUP BY model")
    labelled = await db.fetchall(
        "SELECT label, status = 'lead' AS lead FROM social.captures WHERE label IS NOT NULL")
    agent = None
    try:
        raw = await bus.kv_get("heartbeats", "social")
        agent = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - the screen still works without the agent's status
        agent = None
    return {
        "captures_7d": {r["status"]: r["n"] for r in counts},
        "captures_today": sum(r["today"] for r in counts),
        "groups_30d": groups["groups"] if groups else 0,
        "last_capture": groups["last_capture"] if groups else None,
        "leads": {r["status"]: r["n"] for r in leads_by},
        "posted_7d": posted_7d or 0,
        "spend_month": spend,
        "spend_today_usd": round(sum(r["usd_today"] for r in spend), 4),
        "accuracy": accuracy(labelled),
        "models": {"classify": CLASSIFY_MODEL, "draft": DRAFT_MODEL},
        "agent": {"online": bool(agent), "llm": (agent or {}).get("llm"), "embedder": (agent or {}).get("embedder"),
                  "at": (agent or {}).get("at")},
        "now": datetime.now(UTC),
    }
