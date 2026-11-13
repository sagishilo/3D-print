"""Bambu P1 / A1 camera frame-reader -> HTTP MJPEG, for go2rtc.

P1 and A1 printers have no RTSP. Their camera sends JPEG frames over a TLS socket
on port 6000 (protocol as documented by the community OpenBambuAPI project):
  1. connect with TLS (self-signed printer cert, so no verification)
  2. send an 80-byte auth packet:
       <I 0x40, <I 0x3000, <I 0, <I 0, username "bblp" (32 bytes, NUL padded), access code (32 bytes, NUL padded)
  3. read frames forever: 16-byte header (first 4 bytes = payload length, little endian), then the JPEG

STATUS: written against the published protocol, NOT yet tested on a real printer.
Test this first (README step 9) before relying on P1/A1 cameras.

Serves http://bambu-cam:8090/<printer-id>.mjpeg (multipart/x-mixed-replace) and /<id>.jpg.
The printer connection opens only while someone is watching and closes 30 s after the last viewer leaves.

Config (env):  BAMBU_CAMS = "p1s-01=192.168.50.23,a1-mini=192.168.50.24"
               access codes: BAMBU_CODE_<ID with - replaced by _ and upper-cased>, e.g. BAMBU_CODE_P1S_01
"""
import asyncio
import logging
import os
import ssl
import struct

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bambu-cam")

PORT = 6000
BOUNDARY = b"pfframe"
IDLE_CLOSE_S = 30
MAX_FRAME = 4 * 1024 * 1024


def auth_packet(access_code: str, username: str = "bblp") -> bytes:
    return (struct.pack("<IIII", 0x40, 0x3000, 0, 0)
            + username.encode().ljust(32, b"\0")
            + access_code.encode().ljust(32, b"\0"))


class Camera:
    def __init__(self, cam_id: str, host: str, code: str):
        self.id, self.host, self.code = cam_id, host, code
        self.frame: bytes | None = None
        self.new_frame = asyncio.Condition()
        self.viewers = 0
        self.task: asyncio.Task | None = None

    def _ssl(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # printer uses a self-signed certificate on the LAN
        return ctx

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
                    asyncio.open_connection(self.host, PORT, ssl=self._ssl()), timeout=10)
                writer.write(auth_packet(self.code))
                await writer.drain()
                log.info("%s: connected to %s:%s", self.id, self.host, PORT)
                while not idle_expired():
                    header = await asyncio.wait_for(reader.readexactly(16), timeout=15)
                    size = struct.unpack("<I", header[:4])[0]
                    if not 0 < size <= MAX_FRAME:
                        raise ValueError(f"bad frame size {size}")
                    jpeg = await reader.readexactly(size)
                    if jpeg[:2] != b"\xff\xd8":
                        raise ValueError("payload is not a JPEG (wrong access code or protocol?)")
                    async with self.new_frame:
                        self.frame = jpeg
                        self.new_frame.notify_all()
                log.info("%s: no viewers for %s s, closing", self.id, IDLE_CLOSE_S)
                writer.close()
                self.task = None
                return
            except Exception as exc:  # noqa: BLE001
                if writer:
                    writer.close()
                if idle_expired():
                    self.task = None
                    return
                log.warning("%s: %s; retrying in 3 s", self.id, exc)
                await asyncio.sleep(3)

    def ensure_running(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())


def load_cameras() -> dict[str, Camera]:
    cams = {}
    for item in filter(None, (x.strip() for x in os.environ.get("BAMBU_CAMS", "").split(","))):
        cam_id, host = item.split("=", 1)
        code = os.environ.get(f"BAMBU_CODE_{cam_id.replace('-', '_').upper()}", "")
        if not code:
            log.error("%s: missing access code env var, skipped", cam_id)
            continue
        cams[cam_id] = Camera(cam_id, host, code)
    return cams


CAMS = load_cameras()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        request_line = (await reader.readline()).decode(errors="replace").split()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        path = request_line[1] if len(request_line) > 1 else "/"
        if path == "/health":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok")
            return
        name, _, ext = path.lstrip("/").rpartition(".")
        cam = CAMS.get(name)
        if not cam or ext not in ("mjpeg", "jpg"):
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            return
        cam.viewers += 1
        cam.ensure_running()
        try:
            if ext == "jpg":
                async with cam.new_frame:
                    await asyncio.wait_for(cam.new_frame.wait_for(lambda: cam.frame is not None), timeout=15)
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(cam.frame) + cam.frame)
                return
            writer.write(b"HTTP/1.1 200 OK\r\nCache-Control: no-store\r\n"
                         b"Content-Type: multipart/x-mixed-replace; boundary=" + BOUNDARY + b"\r\n\r\n")
            while True:
                async with cam.new_frame:
                    await asyncio.wait_for(cam.new_frame.wait(), timeout=30)
                    frame = cam.frame
                writer.write(b"--" + BOUNDARY + b"\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame)
                             + frame + b"\r\n")
                await writer.drain()
        finally:
            cam.viewers -= 1
    except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            await writer.drain()
            writer.close()
        except Exception:  # noqa: BLE001
            pass


async def main():
    log.info("cameras configured: %s", ", ".join(CAMS) or "none")
    server = await asyncio.start_server(handle, "0.0.0.0", 8090)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
