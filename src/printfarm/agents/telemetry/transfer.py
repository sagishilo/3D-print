"""Plate-file transfer to printers.

* Bambu Lab: FTP over **implicit** TLS on port 990, user ``bblp``, password = the LAN access code. The printer's
  FTP server (vsftpd) is commonly reported to require TLS session reuse on the data connection, so the data socket
  reuses the control connection's TLS session. TLS is capped at 1.2, which these printers speak.
* Klipper/Moonraker: ``POST /server/files/upload`` (multipart) on the same host:port as the WebSocket API.

``ftplib`` is blocking; uploads run in a worker thread so the event loop (and with it the safety detectors on the
telemetry hot path) never waits for a slow transfer.
"""

from __future__ import annotations

import asyncio
import contextlib
import ftplib
import hashlib
import io
import ssl
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


class UploadError(RuntimeError):
    """The plate file could not be transferred; the message is shown to the operator.

    ``transient`` = a retry may succeed (timeout, connection drop); permanent = wrong access code, no space, refused.
    """

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient


@dataclass(frozen=True)
class PlateFile:
    plate_id: str
    path: Path            # local file
    remote_name: str      # name on the printer, e.g. plate-1a2b.gcode.3mf
    md5: str
    size: int
    plate_gcode: str = "Metadata/plate_1.gcode"  # Bambu: the sliced plate inside the 3MF
    # from the slicer (Bambu sliced files): (slicer filament id, type, "#RRGGBB") per filament the plate uses,
    # the printer model it was sliced for and its height
    filaments: tuple[tuple[int, str, str], ...] = ()
    printer_model: str | None = None
    max_z_mm: float | None = None


def md5_of(path: Path) -> str:
    h = hashlib.md5()  # noqa: S324 - integrity check the printer protocol asks for, not security
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- Bambu: implicit FTPS


def printer_tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # printers present a self-signed certificate on the LAN
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class ImplicitFtpTls(ftplib.FTP_TLS):
    """FTP_TLS with implicit TLS (the control socket is TLS from the first byte) and TLS session reuse."""

    def __init__(self, *, context: ssl.SSLContext, timeout: float, reuse_session: bool = True):
        super().__init__(context=context, timeout=timeout)
        self._sock = None
        self.reuse_session = reuse_session

    @property
    def sock(self):  # type: ignore[override]
        return self._sock

    @sock.setter
    def sock(self, value):
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)  # plain socket; wrap it ourselves
        if self._prot_p:
            session = self.sock.session if self.reuse_session else None
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=session)
        return conn, size

    def store(self, name: str, fh, blocksize: int = 64 * 1024) -> str:
        """STOR with a bounded TLS shutdown: some printer FTP servers never answer close_notify."""
        self.voidcmd("TYPE I")
        conn = self.transfercmd(f"STOR {name}")
        try:
            while chunk := fh.read(blocksize):
                conn.sendall(chunk)
            if isinstance(conn, ssl.SSLSocket):
                conn.settimeout(5)
                with contextlib.suppress(OSError, ValueError):
                    conn.unwrap()
        finally:
            conn.close()
        return self.voidresp()


def _ftps_upload_blocking(host: str, port: int, access_code: str, data: io.BufferedIOBase, remote_name: str,
                          size: int, timeout: float, reuse_session: bool) -> None:
    ftp = ImplicitFtpTls(context=printer_tls_context(), timeout=timeout, reuse_session=reuse_session)
    try:
        ftp.connect(host, port)
        ftp.login("bblp", access_code)
        ftp.prot_p()
        ftp.store(remote_name, data)
        try:
            remote_size = ftp.size(remote_name)
        except ftplib.error_perm:  # SIZE not supported: nothing to compare
            remote_size = None
        if remote_size is not None and remote_size != size:
            raise UploadError(f"printer reports {remote_size} bytes for {remote_name}, sent {size}")
    except ftplib.error_perm as exc:  # 5xx: wrong access code, no space, refused
        raise UploadError(f"printer refused the upload: {exc}") from exc
    except ftplib.Error as exc:  # 4xx and protocol surprises
        raise UploadError(f"FTP error: {exc}", transient=True) from exc
    except (OSError, EOFError) as exc:  # includes ssl.SSLError and socket timeouts
        raise UploadError(f"could not reach the printer's FTPS server at {host}:{port}: {exc!r}", transient=True) from exc
    finally:
        with contextlib.suppress(Exception):
            ftp.quit()
        with contextlib.suppress(Exception):
            ftp.close()


async def upload_ftps(plate: PlateFile, *, host: str, port: int, access_code: str, timeout: float = 600,
                      reuse_session: bool = True) -> float:
    """Upload ``plate`` to a Bambu printer; returns the seconds it took."""
    t0 = time.monotonic()

    def run() -> None:
        with plate.path.open("rb") as fh:
            _ftps_upload_blocking(host, port, access_code, fh, plate.remote_name, plate.size,
                                  timeout=min(timeout, 60), reuse_session=reuse_session)

    try:
        await asyncio.wait_for(asyncio.to_thread(run), timeout)
    except TimeoutError as exc:
        raise UploadError(f"upload of {plate.remote_name} took longer than {timeout:.0f} s", transient=True) from exc
    return time.monotonic() - t0


# ---------------------------------------------------------------- Moonraker: HTTP


async def upload_moonraker(plate: PlateFile, *, base_url: str, api_key: str = "", timeout: float = 600) -> float:
    """Upload ``plate`` to Moonraker's gcodes root (without starting it); returns the seconds it took."""
    t0 = time.monotonic()
    headers = {"X-Api-Key": api_key} if api_key else {}
    data = await asyncio.to_thread(plate.path.read_bytes)
    try:
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout) as client:
            r = await client.post("/server/files/upload", data={"root": "gcodes"},
                                  files={"file": (plate.remote_name, data, "application/octet-stream")})
    except httpx.HTTPError as exc:
        raise UploadError(f"could not reach Moonraker at {base_url}: {exc!r}", transient=True) from exc
    if r.status_code not in (200, 201):
        transient = r.status_code >= 500 and r.status_code != 507  # 507 = no space left
        raise UploadError(f"Moonraker refused the upload: HTTP {r.status_code} {r.text[:200]}", transient=transient)
    return time.monotonic() - t0
