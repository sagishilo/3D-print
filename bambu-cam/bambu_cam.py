"""Bambu P1 / A1 camera frame-reader -> HTTP MJPEG, for go2rtc.

P1 and A1 printers have no RTSP. Their camera sends JPEG frames over a TLS socket
on port 6000 (protocol as documented by the community OpenBambuAPI project):
  1. connect with TLS (self-signed printer cert, so no verification)
  2. send an 80-byte auth packet:
       <I 0x40, <I 0x3000, <I 0, <I 0, username "bblp" (32 bytes, NUL padded), access code (32 bytes, NUL padded)
  3. read frames forever: 16-byte header (<I payload length, <I 0, <I 1, <I 0, little endian), then the JPEG

STATUS: tested against the PrintFarm printer simulator (including dropped, stalled and refused connections),
NOT yet on a real printer. Test it on one printer first (README step 12) before relying on P1/A1 cameras.

Serves http://bambu-cam:8090/<printer-id>.mjpeg (multipart/x-mixed-replace), /<id>.jpg (one fresh frame) and
/status (JSON per camera). The printer connection opens only while someone is watching and closes
BAMBU_CAM_IDLE_CLOSE seconds after the last viewer leaves.

Config (env):  BAMBU_CAMS = "p1s-01=192.168.50.23,a1-mini=192.168.50.24"   (host or host:port, default port 6000)
               access codes: BAMBU_CODE_<ID with - replaced by _ and upper-cased>, e.g. BAMBU_CODE_P1S_01
               optional: BAMBU_CAM_READ_TIMEOUT (15 s), BAMBU_CAM_RETRY (3 s), BAMBU_CAM_IDLE_CLOSE (30 s)
"""
import asyncio
import json
import logging
import os
import ssl
import struct
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bambu-cam")

PORT = 6000
BOUNDARY = b"pfframe"
MAX_FRAME = 4 * 1024 * 1024
READ_TIMEOUT_S = float(os.environ.get("BAMBU_CAM_READ_TIMEOUT", "15"))
RETRY_S = float(os.environ.get("BAMBU_CAM_RETRY", "3"))
IDLE_CLOSE_S = float(os.environ.get("BAMBU_CAM_IDLE_CLOSE", "30"))


def auth_packet(access_code: str, username: str = "bblp") -> bytes:
    return (struct.pack("<IIII", 0x40, 0x3000, 0, 0)
            + username.encode().ljust(32, b"\0")
            + access_code.encode().ljust(32, b"\0"))


class Camera:
    def __init__(self, cam_id: str, host: str, code: str, port: int = PORT):
        self.id, self.host, self.port, self.code = cam_id, host, port, code
        self.frame: bytes | None = None          # newest frame of the CURRENT connection, None when disconnected
        self.new_frame = asyncio.Condition()
        self.viewers = 0
        self.task: asyncio.Task | None = None
        self.connected = False
        self.frames = 0
        self.last_error = ""
        self.last_frame_at = 0.0

    def _ssl(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # printer uses a self-signed certificate on the LAN
        return ctx

    def status(self) -> dict:
        return {"host": f"{self.host}:{self.port}", "connected": self.connected, "viewers": self.viewers,
                "frames": self.frames, "last_error": self.last_error,
                "last_frame_age_s": round(time.time() - self.last_frame_at, 1) if self.last_frame_at else None}

    async def _disconnected(self, writer) -> None:
        if writer:
            writer.close()
        self.connected = False
        async with self.new_frame:
            self.frame = None  # never serve a frame from a connection that is gone
            self.new_frame.notify_all()

    async def _run(self):
        loop = asyncio.get_running_loop()
        last_viewer = loop.time()

        def idle_expired() -> bool:
            nonlocal last_viewer
            if self.viewers > 0:
                last_viewer = loop.time()
            return loop.time() - last_viewer > IDLE_CLOSE_S

        while True:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port, ssl=self._ssl()), timeout=10)
                writer.write(auth_packet(self.code))
                await writer.drain()
                log.info("%s: connected to %s:%s", self.id, self.host, self.port)
                self.connected, self.last_error = True, ""
                while not idle_expired():
                    header = await asyncio.wait_for(reader.readexactly(16), timeout=READ_TIMEOUT_S)
                    size = struct.unpack("<I", header[:4])[0]
                    if not 0 < size <= MAX_FRAME:
                        raise ValueError(f"bad frame size {size}")
                    jpeg = await asyncio.wait_for(reader.readexactly(size), timeout=READ_TIMEOUT_S)
                    if jpeg[:2] != b"\xff\xd8":
                        raise ValueError("payload is not a JPEG (wrong access code or protocol?)")
                    async with self.new_frame:
                        self.frame = jpeg
                        self.frames += 1
                        self.last_frame_at = time.time()
                        self.new_frame.notify_all()
                log.info("%s: no viewers for %s s, closing", self.id, IDLE_CLOSE_S)
                await self._disconnected(writer)
                self.task = None
                return
            except Exception as exc:  # noqa: BLE001
                self.last_error = "no frame received (timeout)" if isinstance(exc, TimeoutError) else str(exc)
                await self._disconnected(writer)
                if idle_expired():
                    self.task = None
                    return
                log.warning("%s: %s; retrying in %s s", self.id, self.last_error, RETRY_S)
                await asyncio.sleep(RETRY_S)

    def ensure_running(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())


def parse_cams(spec: str) -> list[tuple[str, str, int]]:
    """'p1s-01=192.168.50.23,sim=simulator:6001' -> [(id, host, port), ...]"""
    out = []
    for item in filter(None, (x.strip() for x in spec.split(","))):
        cam_id, _, addr = item.partition("=")
        host, _, port = addr.strip().rpartition(":") if ":" in addr else (addr.strip(), "", "")
        out.append((cam_id.strip(), host, int(port) if port else PORT))
    return out


def load_cameras() -> dict[str, Camera]:
    cams = {}
    for cam_id, host, port in parse_cams(os.environ.get("BAMBU_CAMS", "")):
        code = os.environ.get(f"BAMBU_CODE_{cam_id.replace('-', '_').upper()}", "")
        if not code:
            log.error("%s: missing access code env var, skipped", cam_id)
            continue
        cams[cam_id] = Camera(cam_id, host, code, port)
    return cams


CAMS = load_cameras()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, cams: dict[str, Camera] | None = None):
    cams = CAMS if cams is None else cams
    try:
        request_line = (await reader.readline()).decode(errors="replace").split()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        path = request_line[1] if len(request_line) > 1 else "/"
        if path == "/health":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok")
            return
        if path == "/status":
            body = json.dumps({cid: c.status() for cid, c in cams.items()}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body)
                         + body)
            return
        name, _, ext = path.lstrip("/").rpartition(".")
        cam = cams.get(name)
        if not cam or ext not in ("mjpeg", "jpg"):
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            return
        cam.viewers += 1
        cam.ensure_running()
        try:
            # First a fresh frame - or fail fast with the reason when the printer refuses the camera (wrong code,
            # unsupported firmware), so go2rtc and the dashboard show the cause instead of waiting for a timeout.
            async with cam.new_frame:
                await asyncio.wait_for(cam.new_frame.wait_for(
                    lambda: cam.frame is not None or (bool(cam.last_error) and not cam.connected)), timeout=15)
                frame = cam.frame
            if frame is None:
                msg = f"camera {cam.id}: {cam.last_error}".encode()
                writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n"
                             b"Content-Length: %d\r\n\r\n" % len(msg) + msg)
                return
            if ext == "jpg":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame) + frame)
                return
            writer.write(b"HTTP/1.1 200 OK\r\nCache-Control: no-store\r\n"
                         b"Content-Type: multipart/x-mixed-replace; boundary=" + BOUNDARY + b"\r\n\r\n")
            writer.write(b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame)
                         + frame + b"\r\n")
            await writer.drain()
            while True:
                async with cam.new_frame:
                    await asyncio.wait_for(cam.new_frame.wait(), timeout=30)
                    frame = cam.frame
                if frame is None:  # the printer connection dropped; keep the viewer, wait for the next frame
                    continue
                writer.write(b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame)
                             + frame + b"\r\n")
                await writer.drain()
        finally:
            cam.viewers -= 1
    except (ConnectionError, TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            await writer.drain()
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def main():
    log.info("cameras configured: %s", ", ".join(f"{c.id}={c.host}:{c.port}" for c in CAMS.values()) or "none")
    server = await asyncio.start_server(handle, "0.0.0.0", 8090)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
