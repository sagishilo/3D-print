"""Static simulator assets: a TEST-ONLY TLS certificate and a few JPEG camera frames."""

from __future__ import annotations

import ssl
from functools import cache
from importlib import resources


def asset(name: str) -> bytes:
    return resources.files("printfarm.simulator").joinpath("assets", name).read_bytes()


@cache
def camera_frames() -> tuple[bytes, ...]:
    return tuple(asset(f"frame-{i}.jpg") for i in range(1, 5))


def server_tls_context() -> ssl.SSLContext:
    """Server context for the simulated printer's FTPS and camera ports.

    TLS 1.2 like the printers; one context per server so TLS sessions can be resumed across its connections
    (the FTPS data channel must resume the control connection's session).
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    with resources.as_file(resources.files("printfarm.simulator").joinpath("assets", "sim-printer.pem")) as pem:
        ctx.load_cert_chain(pem)
    return ctx
