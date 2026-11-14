"""Camera proxy: go2rtc -> gateway -> browser (MJPEG and snapshots), login required."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import auth
from .commands import PRINTER_ID as CAMERA_ID  # go2rtc stream names are the printer ids

router = APIRouter(prefix="/api/v1/cameras", dependencies=[Depends(auth.require_auth)])


def _relay_error(cam_id: str, exc: httpx.HTTPError) -> HTTPException:
    """A camera that sends nothing is a 504, an unreachable relay a 502 - never an unhandled 500."""
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(504, f"camera {cam_id} sent no frame in time")
    return HTTPException(502, f"camera relay unreachable ({type(exc).__name__})")


@router.get("/{cam_id}/mjpeg")
async def camera_mjpeg(cam_id: str, request: Request):
    if not CAMERA_ID.match(cam_id):
        raise HTTPException(400, "bad camera id")
    client: httpx.AsyncClient = request.app.state.http
    try:
        upstream = await client.send(client.build_request("GET", "/api/stream.mjpeg", params={"src": cam_id},
                                                          timeout=httpx.Timeout(10, read=None)), stream=True)
    except httpx.HTTPError as exc:
        raise _relay_error(cam_id, exc) from exc
    if upstream.status_code != 200:
        await upstream.aclose()
        raise HTTPException(502, f"go2rtc returned {upstream.status_code} for {cam_id}")

    async def body():
        try:
            async for chunk in upstream.aiter_raw():
                if await request.is_disconnected():  # tab closed: stop, so go2rtc stops reading the camera
                    break
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(body(), media_type=upstream.headers.get("content-type", "multipart/x-mixed-replace"),
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/{cam_id}/snapshot.jpg")
async def camera_snapshot(cam_id: str, request: Request):
    if not CAMERA_ID.match(cam_id):
        raise HTTPException(400, "bad camera id")
    try:
        r = await request.app.state.http.get("/api/frame.jpeg", params={"src": cam_id}, timeout=10)
    except httpx.HTTPError as exc:
        raise _relay_error(cam_id, exc) from exc
    if r.status_code != 200 or not r.content.startswith(b"\xff\xd8"):  # go2rtc can answer 200 with an empty body
        raise HTTPException(502, f"no frame from {cam_id}")
    return StreamingResponse(iter([r.content]), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("")
async def cameras(request: Request):
    try:
        r = await request.app.state.http.get("/api/streams", timeout=5)
    except httpx.HTTPError as exc:
        raise _relay_error("list", exc) from exc
    return {"cameras": sorted(r.json().keys()) if r.status_code == 200 else []}
