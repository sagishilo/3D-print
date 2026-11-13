"""Simulated printer cameras.

* Bambu P1/A1 "port 6000" protocol (TLS): the viewer sends an 80-byte auth packet
  (<I 0x40, <I 0x3000, <I 0, <I 0, user "bblp" padded to 32 bytes, access code padded to 32 bytes); the printer then
  sends frames, each a 16-byte header (<I payload size, <I 0, <I 1, <I 0) followed by one JPEG.
* Klipper/Crowsnest MJPEG over HTTP (multipart/x-mixed-replace), served by the Moonraker simulator.

Faults (SIM_FAULTS, per printer):
    camera_off        the camera port is closed / the webcam answers 503
    camera_auth_fail  the login is refused: the answer is not a JPEG (what bambu-cam reports for a wrong code)
    camera_drop       the connection closes after a few frames (viewers must reconnect)
    camera_stall      frames stop after a few, the connection stays open (viewers must time out)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import struct
from typing import TYPE_CHECKING

from printfarm.simulator.assets import camera_frames

if TYPE_CHECKING:
    from printfarm.simulator.printers import SimPrinter

log = logging.getLogger("simulator.camera")

AUTH_PACKET_SIZE = 80
DROP_AFTER = 5     # frames before camera_drop closes the connection
STALL_AFTER = 3    # frames before camera_stall stops sending
REFUSED = b"\x00" * 4 + b"login refused (simulated)"


def frame_header(size: int) -> bytes:
    return struct.pack("<IIII", size, 0, 1, 0)


def parse_auth(packet: bytes) -> tuple[str, str] | None:
    """(user, access code) from the 80-byte auth packet, or None if it is malformed."""
    if len(packet) != AUTH_PACKET_SIZE:
        return None
    size, kind, _flags, _zero = struct.unpack("<IIII", packet[:16])
    if size != 0x40 or kind != 0x3000:
        return None
    return packet[16:48].rstrip(b"\0").decode(errors="replace"), packet[48:80].rstrip(b"\0").decode(errors="replace")


class BambuCameraServer:
    def __init__(self, printer: SimPrinter, ctx: ssl.SSLContext, *, fps: float = 2.0):
        self.printer, self.ctx, self.fps = printer, ctx, fps
        self.server: asyncio.Server | None = None
        self.viewers = 0

    async def start(self, host: str, port: int) -> asyncio.Server:
        self.server = await asyncio.start_server(self._client, host, port, ssl=self.ctx)
        return self.server

    @property
    def port(self) -> int:
        return self.server.sockets[0].getsockname()[1]

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        p = self.printer
        self.viewers += 1
        try:
            try:
                creds = parse_auth(await asyncio.wait_for(reader.readexactly(AUTH_PACKET_SIZE), 10))
            except (TimeoutError, asyncio.IncompleteReadError):
                return
            authorized = creds is not None and creds[0] == "bblp" and (not p.access_code or creds[1] == p.access_code)
            if not authorized or p.has("camera_auth_fail"):
                log.warning("%s: camera login refused", p.id)
                writer.write(frame_header(len(REFUSED)) + REFUSED)
                await writer.drain()
                return
            sent = 0
            frames = camera_frames()
            while True:
                if p.has("camera_drop") and sent >= DROP_AFTER:
                    log.info("%s: camera connection dropped (simulated)", p.id)
                    return
                if p.has("camera_stall") and sent >= STALL_AFTER:
                    await asyncio.sleep(3600)  # silent but open
                    continue
                frame = frames[sent % len(frames)]
                writer.write(frame_header(len(frame)) + frame)
                await writer.drain()
                sent += 1
                await asyncio.sleep(1 / self.fps)
        except (ConnectionError, ssl.SSLError):
            pass
        finally:
            self.viewers -= 1
            with contextlib.suppress(Exception):
                writer.close()


async def write_mjpeg(writer: asyncio.StreamWriter, printer: SimPrinter, *, fps: float = 5.0) -> None:
    """Crowsnest-style MJPEG stream (multipart/x-mixed-replace) until the client leaves or a fault ends it."""
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                 b"Cache-Control: no-store\r\nConnection: close\r\n\r\n")
    frames, sent = camera_frames(), 0
    while True:
        if printer.has("camera_drop") and sent >= DROP_AFTER:
            return
        if printer.has("camera_stall") and sent >= STALL_AFTER:
            await asyncio.sleep(3600)
            continue
        frame = frames[sent % len(frames)]
        writer.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame) + frame + b"\r\n")
        await writer.drain()
        sent += 1
        await asyncio.sleep(1 / fps)
