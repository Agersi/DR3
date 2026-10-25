"""Refresh DR3 branding assets from images/dr3_256.png.

Writes:
  web/static/logo.png  (transparent website logo)
  icon_32.png          (32x32 Tk runtime icon extracted from the Windows icon)
  icon.ico             (copied from images/favicon.ico when present; otherwise generated)

Pure stdlib. Run from the dr3/ root:
    python scripts/update_icon_from_logo.py
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "images" / "dr3_256.png"
LOGO = ROOT / "web" / "static" / "logo.png"
ICON32 = ROOT / "icon_32.png"
ICO = ROOT / "icon.ico"
PROVIDED_ICO = ROOT / "images" / "favicon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def read_png_rgba(path: Path) -> tuple[int, int, list[bytearray]]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")

    pos = 8
    idat = b""
    plte = b""
    trans: bytes | None = None
    width = height = bit_depth = color_type = interlace = 0
    while pos + 8 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        if tag == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
        elif tag == b"IDAT":
            idat += chunk
        elif tag == b"PLTE":
            plte = chunk
        elif tag == b"tRNS":
            trans = chunk
        pos += 12 + length

    if bit_depth != 8 or interlace != 0:
        raise ValueError("only non-interlaced 8-bit PNGs are supported")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError(f"unsupported PNG color type {color_type}")

    raw = zlib.decompress(idat)
    line_length = width * channels
    out = bytearray()
    prev = bytearray(line_length)
    i = 0
    for _ in range(height):
        filter_type = raw[i]
        i += 1
        line = bytearray(raw[i : i + line_length])
        i += line_length

        if filter_type == 0:
            pass
        elif filter_type == 1:
            for x in range(channels, line_length):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif filter_type == 2:
            for x in range(line_length):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif filter_type == 3:
            for x in range(line_length):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif filter_type == 4:
            for x in range(line_length):
                left = line[x - channels] if x >= channels else 0
                up = prev[x]
                upper_left = prev[x - channels] if x >= channels else 0
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                if pa <= pb and pa <= pc:
                    pr = left
                elif pb <= pc:
                    pr = up
                else:
                    pr = upper_left
                line[x] = (line[x] + pr) & 0xFF
        else:
            raise ValueError(f"unsupported PNG filter {filter_type}")

        out += line
        prev = line

    rows: list[bytearray] = []
    for y in range(height):
        row = bytearray()
        base = y * line_length
        for x in range(width):
            idx = base + x * channels
            if color_type == 6:
                r, g, b, a = out[idx : idx + 4]
            elif color_type == 2:
                r, g, b = out[idx : idx + 3]
                a = 255
            elif color_type == 4:
                gray, a = out[idx : idx + 2]
                r = g = b = gray
            elif color_type == 0:
                r = g = b = out[idx]
                a = 255
            else:  # palette
                palette_idx = out[idx]
                pr = plte[palette_idx * 3]
                pg = plte[palette_idx * 3 + 1]
                pb = plte[palette_idx * 3 + 2]
                pa = trans[palette_idx] if trans and palette_idx < len(trans) else 255
                r, g, b, a = pr, pg, pb, pa
            row += bytes((r, g, b, a))
        rows.append(row)
    return width, height, rows


def resize(
    src_w: int, src_h: int, src_rows: list[bytearray], dst_w: int, dst_h: int
) -> list[bytearray]:
    if src_w == dst_w and src_h == dst_h:
        return [bytes(row) for row in src_rows]

    dst_rows: list[bytearray] = []
    for y in range(dst_h):
        y0 = int(y * src_h / dst_h)
        y1 = int((y + 1) * src_h / dst_h)
        if y1 <= y0:
            y1 = y0 + 1
        y1 = min(y1, src_h)

        row = bytearray()
        for x in range(dst_w):
            x0 = int(x * src_w / dst_w)
            x1 = int((x + 1) * src_w / dst_w)
            if x1 <= x0:
                x1 = x0 + 1
            x1 = min(x1, src_w)

            r_sum = g_sum = b_sum = a_sum = 0.0
            count = 0
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    idx = xx * 4
                    a = src_rows[yy][idx + 3] / 255.0
                    r_sum += src_rows[yy][idx] * a
                    g_sum += src_rows[yy][idx + 1] * a
                    b_sum += src_rows[yy][idx + 2] * a
                    a_sum += a
                    count += 1

            if a_sum > 0 and count > 0:
                r = int(round(r_sum / a_sum))
                g = int(round(g_sum / a_sum))
                b = int(round(b_sum / a_sum))
                a = int(round((a_sum / count) * 255))
            else:
                r = g = b = a = 0
            row += bytes((r, g, b, a))
        dst_rows.append(row)
    return dst_rows


def to_png(width: int, height: int, rows: list[bytes | bytearray]) -> bytes:
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def to_dib(size: int, rows: list[bytes | bytearray]) -> bytes:
    and_row_bytes = ((size + 31) // 32) * 4
    xor_size = size * size * 4
    header = struct.pack(
        "<IiiHHIIiiii",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        xor_size + and_row_bytes * size,
        0,
        0,
        0,
        0,
    )
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        row = rows[y]
        for x in range(size):
            idx = x * 4
            r, g, b, a = row[idx : idx + 4]
            xor += bytes((b, g, r, a))
    return header + bytes(xor) + bytes(and_row_bytes * size)


def to_ico(images: dict[int, bytes]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    payload = b""
    offset = 6 + 16 * len(images)
    for size in sorted(images):
        data = images[size]
        dim = 0 if size == 256 else size
        entries += struct.pack(
            "<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset
        )
        payload += data
        offset += len(data)
        pad = (-len(data)) % 4
        if pad:
            payload += b"\x00" * pad
            offset += pad
    return header + entries + payload


def ico_entries(path: Path) -> list[tuple[int, bytes]]:
    data = path.read_bytes()
    if data[:4] != b"\x00\x00\x01\x00":
        raise ValueError(f"not an ICO file: {path}")
    count = struct.unpack("<H", data[4:6])[0]
    entries = []
    for i in range(count):
        width, height, _, _, _, _, length, offset = struct.unpack(
            "<BBBBHHII", data[6 + i * 16 : 22 + i * 16]
        )
        entries.append((width or 256, data[offset : offset + length]))
    return entries


def ico_dib_rows(data: bytes, size: int) -> list[bytearray]:
    if len(data) < 40:
        raise ValueError("ICO image is too small")
    _, _, bi_height, _, bpp, _, _, _, _, _, _ = struct.unpack(
        "<IiiHHIIiiii", data[:40]
    )
    if bpp != 32:
        raise ValueError(f"unsupported ICO bit depth {bpp}")
    xor_height = bi_height // 2 if bi_height > size else bi_height
    rows = []
    for y in range(size):
        src_y = xor_height - 1 - y
        base = 40 + src_y * size * 4
        row = bytearray()
        for x in range(size):
            idx = base + x * 4
            b, g, r, a = data[idx : idx + 4]
            row += bytes((r, g, b, a))
        rows.append(row)
    return rows


def runtime_icon_rows() -> list[bytearray]:
    if PROVIDED_ICO.is_file():
        entries = ico_entries(PROVIDED_ICO)
        for size, data in sorted(entries, key=lambda item: abs(item[0] - 32)):
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                continue
            rows = ico_dib_rows(data, size)
            if size != 32:
                rows = resize(size, size, rows, 32, 32)
            return rows
    width, height, rows = read_png_rgba(SOURCE)
    return resize(width, height, rows, 32, 32)


def main() -> None:
    source_bytes = SOURCE.read_bytes()
    LOGO.parent.mkdir(parents=True, exist_ok=True)
    LOGO.write_bytes(source_bytes)
    print(f"wrote {LOGO} ({LOGO.stat().st_size} bytes)")

    ICON32.write_bytes(to_png(32, 32, runtime_icon_rows()))
    print(f"wrote {ICON32} ({ICON32.stat().st_size} bytes)")

    if PROVIDED_ICO.is_file():
        ico_bytes = PROVIDED_ICO.read_bytes()
        ICO.write_bytes(ico_bytes)
        print(f"wrote {ICO} ({len(ico_bytes)} bytes, copied from {PROVIDED_ICO.name})")
        return

    width, height, rows = read_png_rgba(SOURCE)
    images = {}
    for size in SIZES:
        resized = resize(width, height, rows, size, size)
        if size == 256:
            images[size] = to_png(size, size, resized)
        else:
            images[size] = to_dib(size, resized)

    ICO.write_bytes(to_ico(images))
    print(f"wrote {ICO} ({ICO.stat().st_size} bytes, sizes {SIZES})")


if __name__ == "__main__":
    main()
