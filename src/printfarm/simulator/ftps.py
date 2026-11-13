"""Simulated Bambu printer FTP server: implicit TLS (like port 990 on the printer), user ``bblp`` + access code.

Like the vsftpd on the printers it refuses data connections that do not resume the control connection's TLS
session ("522 SSL connection failed: session reuse required"), so a client that works here does the same thing a
real printer needs. Files are kept in the simulated printer's memory (``SimPrinter.files``).

Faults (SIM_FAULTS): ``upload_fail`` = every STOR is refused (no space); ``upload_flaky`` = the first STOR of each
file fails with a temporary error (the client should retry).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from printfarm.simulator.printers import SimPrinter

log = logging.getLogger("simulator.ftps")
OPEN_COMMANDS = {"USER", "PASS", "QUIT", "FEAT", "SYST", "NOOP", "OPTS"}


@dataclass
class _Session:
    user: str = ""
    authed: bool = False
    prot_p: bool = False
    data_server: asyncio.Server | None = None
    data_conn: asyncio.Future | None = None


class SimFtpsServer:
    def __init__(self, printer: SimPrinter, ctx: ssl.SSLContext, *, require_session_reuse: bool = True):
        self.printer, self.ctx, self.require_session_reuse = printer, ctx, require_session_reuse
        self.server: asyncio.Server | None = None

    async def start(self, host: str, port: int) -> asyncio.Server:
        self.server = await asyncio.start_server(self._control, host, port, ssl=self.ctx)
        return self.server

    @property
    def port(self) -> int:
        return self.server.sockets[0].getsockname()[1]

    # ------------------------------------------------------------ control connection

    async def _control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        s = _Session()
        await self._reply(writer, "220 PrintFarm simulated printer FTPS ready")
        try:
            while line := await reader.readline():
                cmd, _, arg = line.decode(errors="replace").rstrip("\r\n").partition(" ")
                if await self._command(s, cmd.upper(), arg.strip(), writer):
                    break
        except (ConnectionError, ssl.SSLError, asyncio.IncompleteReadError):
            pass
        finally:
            self._close_data(s)
            with contextlib.suppress(Exception):
                writer.close()

    async def _reply(self, writer: asyncio.StreamWriter, text: str) -> None:
        writer.write(text.encode() + b"\r\n")
        await writer.drain()

    async def _command(self, s: _Session, cmd: str, arg: str, w: asyncio.StreamWriter) -> bool:
        p = self.printer
        if not s.authed and cmd not in OPEN_COMMANDS:
            await self._reply(w, "530 Please login with USER and PASS.")
            return False
        match cmd:
            case "USER":
                s.user, s.authed = arg, False
                await self._reply(w, "331 Please specify the password.")
            case "PASS":
                if s.user == "bblp" and (not p.access_code or arg == p.access_code):
                    s.authed = True
                    await self._reply(w, "230 Login successful.")
                else:
                    log.warning("%s: FTP login refused for user %r", p.id, s.user)
                    await self._reply(w, "530 Login incorrect.")
            case "QUIT":
                await self._reply(w, "221 Goodbye.")
                return True
            case "FEAT":
                await self._reply(w, "211-Features:\r\n PASV\r\n PBSZ\r\n PROT\r\n SIZE\r\n UTF8\r\n211 End")
            case "SYST":
                await self._reply(w, "215 UNIX Type: L8")
            case "NOOP" | "OPTS" | "TYPE":
                await self._reply(w, "200 OK.")
            case "PBSZ":
                await self._reply(w, "200 PBSZ set to 0.")
            case "PROT":
                s.prot_p = arg.upper() == "P"
                await self._reply(w, "200 PROT now Private." if s.prot_p else "521 Data connections must be encrypted.")
            case "PWD":
                await self._reply(w, '257 "/" is the current directory')
            case "CWD":
                await self._reply(w, "250 Directory successfully changed.")
            case "PASV":
                await self._pasv(s, w)
            case "STOR":
                await self._stor(s, _name(arg), w)
            case "SIZE":
                data = p.files.get(_name(arg))
                await self._reply(w, f"213 {len(data)}" if data is not None else "550 Could not get file size.")
            case "DELE":
                ok = p.files.pop(_name(arg), None) is not None
                await self._reply(w, "250 Delete operation successful." if ok else "550 Delete operation failed.")
            case "NLST" | "LIST":
                await self._list(s, w)
            case _:
                await self._reply(w, "502 Command not implemented.")
        return False

    # ------------------------------------------------------------ data connections (passive, TLS, session reuse)

    async def _pasv(self, s: _Session, w: asyncio.StreamWriter) -> None:
        self._close_data(s)
        loop = asyncio.get_running_loop()
        s.data_conn = loop.create_future()

        def on_data(r: asyncio.StreamReader, dw: asyncio.StreamWriter) -> None:
            if not s.data_conn.done():
                s.data_conn.set_result((r, dw))
            else:
                dw.close()

        host = w.get_extra_info("sockname")[0]
        s.data_server = await asyncio.start_server(on_data, host, 0, ssl=self.ctx)
        port = s.data_server.sockets[0].getsockname()[1]
        h = host.replace(".", ",") if "." in host else "127,0,0,1"
        await self._reply(w, f"227 Entering Passive Mode ({h},{port >> 8},{port & 0xFF}).")

    def _close_data(self, s: _Session) -> None:
        if s.data_server:
            s.data_server.close()
            s.data_server = None

    async def _open_data(self, s: _Session, w: asyncio.StreamWriter):
        """Wait for the client's data connection (TLS handshake done) after the 150 reply."""
        if s.data_conn is None or s.data_server is None:
            await self._reply(w, "425 Use PASV first.")
            return None
        try:
            r, dw = await asyncio.wait_for(s.data_conn, 15)
        except TimeoutError:
            await self._reply(w, "425 Failed to establish connection.")
            return None
        finally:
            self._close_data(s)
        ssl_obj = dw.get_extra_info("ssl_object")
        if self.require_session_reuse and not (ssl_obj and ssl_obj.session_reused):
            dw.close()
            await self._reply(w, "522 SSL connection failed: session reuse required")
            return None
        return r, dw

    async def _stor(self, s: _Session, name: str, w: asyncio.StreamWriter) -> None:
        p = self.printer
        if not s.prot_p:
            await self._reply(w, "521 Data connections must be encrypted.")
            return
        await self._reply(w, "150 Ok to send data.")
        conn = await self._open_data(s, w)
        if conn is None:
            return
        r, dw = conn
        chunks = []
        with contextlib.suppress(ConnectionError, ssl.SSLError, asyncio.IncompleteReadError):
            while chunk := await r.read(1 << 16):
                chunks.append(chunk)
        with contextlib.suppress(Exception):
            dw.close()
        data = b"".join(chunks)
        if p.has("upload_fail"):
            await self._reply(w, "552 Transfer aborted: exceeded storage allocation.")
        elif p.has("upload_flaky") and name not in p.flaky_failed:  # first attempt per file only
            p.flaky_failed.add(name)
            await self._reply(w, "451 Transfer aborted: local error in processing.")
        else:
            p.files[name] = data
            log.info("%s: received %s (%d bytes) over FTPS", p.id, name, len(data))
            await self._reply(w, "226 Transfer complete.")

    async def _list(self, s: _Session, w: asyncio.StreamWriter) -> None:
        await self._reply(w, "150 Here comes the directory listing.")
        conn = await self._open_data(s, w)
        if conn is None:
            return
        _, dw = conn
        dw.write("".join(f"{n}\r\n" for n in sorted(self.printer.files)).encode())
        with contextlib.suppress(Exception):
            await dw.drain()
            dw.close()
        await self._reply(w, "226 Directory send OK.")


def _name(arg: str) -> str:
    return arg.strip().lstrip("/").split("/")[-1]
