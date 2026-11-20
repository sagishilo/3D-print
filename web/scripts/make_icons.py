"""Draw the phone app's icons (W2-D PWA) without any image library: a blue tile with a white print nozzle over three
printed layers. Writes web/public/icons/*.png. Run: python web/scripts/make_icons.py"""

from __future__ import annotations

import pathlib
import struct
import zlib

OUT = pathlib.Path(__file__).resolve().parent.parent / "public" / "icons"
BLUE = (0x1F, 0x5F, 0xD6)
WHITE = (0xFF, 0xFF, 0xFF)
DARK = (0x0B, 0x0D, 0x10)


def inside(u: float, v: float) -> bool:
    """The white mark, in a 0..1 square: nozzle body, its tip, and three layers below."""
    if 0.34 <= u <= 0.66 and 0.16 <= v <= 0.40:                          # nozzle body
        return True
    if 0.40 <= v <= 0.52:                                                # tip: narrows downwards
        half = 0.16 - (v - 0.40) * 0.9
        return abs(u - 0.5) <= half
    for top, width in ((0.60, 0.22), (0.68, 0.30), (0.76, 0.38)):       # printed layers
        if top <= v <= top + 0.05 and abs(u - 0.5) <= width:
            return True
    return False


def draw(size: int, *, maskable: bool) -> bytes:
    # a maskable icon keeps its mark inside the middle 80% (the safe zone); the plain one is a rounded tile
    pad = 0.1 if maskable else 0.0
    radius = 0 if maskable else 0.2
    rows = []
    for y in range(size):
        row = bytearray([0])                                             # filter: none
        for x in range(size):
            u, v = (x + 0.5) / size, (y + 0.5) / size
            # rounded corners (transparent outside)
            cx, cy = min(u, 1 - u), min(v, 1 - v)
            if radius and cx < radius and cy < radius and (radius - cx) ** 2 + (radius - cy) ** 2 > radius ** 2:
                row += bytes((0, 0, 0, 0))
                continue
            mu, mv = (u - pad) / (1 - 2 * pad), (v - pad) / (1 - 2 * pad)
            color = WHITE if 0 <= mu <= 1 and 0 <= mv <= 1 and inside(mu, mv) else BLUE
            row += bytes((*color, 255))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, maskable in (("icon-192.png", 192, False), ("icon-512.png", 512, False),
                                 ("maskable-512.png", 512, True), ("apple-touch-icon.png", 180, True)):
        (OUT / name).write_bytes(draw(size, maskable=maskable))
        print(OUT / name)


if __name__ == "__main__":
    main()
