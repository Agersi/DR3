"""Generate the DR3 brand icon (retro rally poster motif).

Pure stdlib — writes a 256x256 logo.png and a multi-size PNG-in-ICO icon.ico.
Design: cream paper, diagonal red-orange stripe with mustard accent,
checkered finish-line band along the bottom.

Run: python scripts/make_icon.py   (from the dr3/ root)
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CREAM = (241, 233, 215)
CARD = (250, 245, 232)
INK = (34, 28, 20)
RED = (212, 71, 15)
MUSTARD = (224, 165, 46)

SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(size: int) -> list[bytearray]:
    """Render the icon at `size` px (resolution-independent design)."""
    cell = size / 16
    band_h = size / 8
    rows: list[bytearray] = []
    for y in range(size):
        row = bytearray()
        for x in range(size):
            d = (x + y - size) / math.sqrt(2)  # signed dist from anti-diagonal
            if abs(d) <= 0.10 * size:
                px = RED
            elif 0.10 * size < d <= 0.16 * size:
                px = MUSTARD
            else:
                px = CREAM
            if y >= size - band_h:
                col = int(x // cell)
                r = int((y - (size - band_h)) // cell)
                px = INK if (col + r) % 2 == 0 else CARD
            row += bytes(px)
        rows.append(row)
    return rows


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def to_png(size: int) -> bytes:
    rows = render(size)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + _png_chunk(b"IDAT", zlib.compress(raw, 9))
            + _png_chunk(b"IEND", b""))


def to_ico() -> bytes:
    blobs = {s: to_png(s) for s in SIZES}
    header = struct.pack("<HHH", 0, 1, len(SIZES))
    entries = b""
    payload = b""
    offset = 6 + 16 * len(SIZES)
    for s in sorted(blobs):
        data = blobs[s]
        dim = 0 if s == 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    return header + entries + payload


def main() -> None:
    logo = ROOT / "web" / "static" / "logo.png"
    logo.parent.mkdir(parents=True, exist_ok=True)
    logo.write_bytes(to_png(256))
    print(f"wrote {logo} ({logo.stat().st_size} bytes)")

    ico = ROOT / "icon.ico"
    ico.write_bytes(to_ico())
    print(f"wrote {ico} ({ico.stat().st_size} bytes, sizes {SIZES})")


if __name__ == "__main__":
    main()
