"""Human approvals (read side) for the dashboard's approvals inbox. Decisions go through the approvals.decide command."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import auth

router = APIRouter(prefix="/api/v1/approvals", dependencies=[Depends(auth.require_auth)])


@router.get("")
async def approvals(request: Request, status: str = "pending", limit: int = Query(50, ge=1, le=200)):
    if status not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(400, "status must be pending, approved, rejected or all")
    rows = await request.app.state.db.fetchall(
        "SELECT approval_id, action, requested_by, summary, details, status, decided_by, note, created_at, decided_at "
        "FROM coordinator.approvals WHERE (%s = 'all' OR status = %s) ORDER BY created_at DESC LIMIT %s",
        (status, status, limit))
    pending = await request.app.state.db.fetchval("SELECT count(*) FROM coordinator.approvals WHERE status = 'pending'")
    return {"approvals": rows, "pending": pending}
