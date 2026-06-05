#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
002.png_to_mag_optimize.py
PNG画像を MAKI02(MAG) 16色画像へ変換する圧縮エンコーダーです。

使い方:
    python 002.png_to_mag.py input.png output.MAG

必要:
    pip install pillow
"""

import sys
from pathlib import Path
from PIL import Image

MAX_COLORS = 16

# MAG仕様のコピー元相対位置。
# 現在位置から (dx, dy) だけ左/上を参照する。
DELTA_X = [0, 1, 2, 4, 0, 1, 0, 1, 2, 0, 1, 2, 0, 1, 2, 0]
DELTA_Y = [0, 0, 0, 0, 1, 1, 2, 2, 2, 4, 4, 4, 8, 8, 8, 16]

# 良好とされている探索順。
SEARCH_ORDER = [1, 4, 5, 6, 7, 9, 10, 2, 8, 11, 12, 13, 14, 3, 15]


def le16(v: int) -> bytes:
    return bytes((v & 0xFF, (v >> 8) & 0xFF))


def le32(v: int) -> bytes:
    return bytes((
        v & 0xFF,
        (v >> 8) & 0xFF,
        (v >> 16) & 0xFF,
        (v >> 24) & 0xFF,
    ))


def make_comment_area() -> bytes:
    # 31バイト + 0x1A で、バイナリヘッダ開始を32にする。
    text = b"MAKI02  Created with Python"
    if len(text) > 31:
        text = text[:31]
    text = text.ljust(31, b" ")
    return text + b"\x1A"


def image_to_indexed_16(src: Path) -> tuple[Image.Image, list[tuple[int, int, int]]]:
    img = Image.open(src)

    if img.mode == "P":
        pal_raw = img.getpalette() or []
        used = sorted(set(img.getdata()))
        if len(used) <= MAX_COLORS and all(0 <= i < MAX_COLORS for i in used):
            palette = []
            for i in range(MAX_COLORS):
                base = i * 3
                if base + 2 < len(pal_raw):
                    palette.append((pal_raw[base], pal_raw[base + 1], pal_raw[base + 2]))
                else:
                    palette.append((0, 0, 0))
            return img, palette

    rgba = img.convert("RGBA")
    indexed = rgba.convert("RGB").quantize(colors=MAX_COLORS, method=Image.Quantize.MEDIANCUT)
    pal_raw = indexed.getpalette() or []

    palette = []
    for i in range(MAX_COLORS):
        base = i * 3
        if base + 2 < len(pal_raw):
            palette.append((pal_raw[base], pal_raw[base + 1], pal_raw[base + 2]))
        else:
            palette.append((0, 0, 0))
    return indexed, palette


def build_mag_palette_grb(palette_rgb: list[tuple[int, int, int]]) -> bytes:
    out = bytearray()
    for i in range(MAX_COLORS):
        r, g, b = palette_rgb[i]
        out.append(rgb8_to_mag8(g))
        out.append(rgb8_to_mag8(r))
        out.append(rgb8_to_mag8(b))
    return bytes(out)

def rgb8_to_mag8(c: int) -> int:
    c4 = (c * 15 + 127) // 255
    return c4 * 17

def make_groups(img: Image.Image) -> list[list[tuple[int, int, int, int]]]:
    width, height = img.size
    pix = list(img.getdata())
    groups_per_line = (width + 3) // 4

    groups = []
    for y in range(height):
        row = []
        row_start = y * width
        for gx in range(groups_per_line):
            vals = []
            x0 = gx * 4
            for i in range(4):
                x = x0 + i
                if x < width:
                    vals.append(int(pix[row_start + x]) & 0x0F)
                else:
                    vals.append(0)
            row.append(tuple(vals))
        groups.append(row)
    return groups


def pack_group(group: tuple[int, int, int, int]) -> bytes:
    return bytes(((group[0] << 4) | group[1], (group[2] << 4) | group[3]))


def get_flag_nibble(line_flags: bytearray, gx: int) -> int:
    b = line_flags[gx // 2]
    if gx & 1:
        return b & 0x0F
    return (b >> 4) & 0x0F


def can_use_flag(groups: list[list[tuple[int, int, int, int]]], x: int, y: int, code: int) -> bool:
    if code <= 0 or code >= 16:
        return False

    sx = x - DELTA_X[code]
    sy = y - DELTA_Y[code]
    if sy < 0 or sx < 0:
        return False
    if sy >= len(groups):
        return False
    if sx >= len(groups[sy]):
        return False

    return groups[sy][sx] == groups[y][x]


def choose_flag(
    groups: list[list[tuple[int, int, int, int]]],
    x: int,
    y: int,
    prev_raw_line: bytearray | None,
) -> int:
    # まず「1つ上の同じ位置のFlag値」と同じにできないか
    # 調べると圧縮率が良いらしい。
    # これは後で縦XORを取るため、同じFlagが縦に並ぶほどFlagBが減るため。
    if prev_raw_line is not None:
        above_code = get_flag_nibble(prev_raw_line, x)
        if can_use_flag(groups, x, y, above_code):
            return above_code

    for code in SEARCH_ORDER:
        if can_use_flag(groups, x, y, code):
            return code
    return 0


def build_compressed_streams(img: Image.Image) -> tuple[bytes, bytes, bytes, dict]:
    width, height = img.size
    groups = make_groups(img)
    groups_per_line = len(groups[0])
    flag_bytes_per_line = (groups_per_line + 1) // 2

    # まず、通常状態のFlagバイト列を作る。
    raw_flags_by_line: list[bytearray] = []
    pixel_data = bytearray()
    code_counts = [0] * 16

    for y in range(height):
        line_flags = bytearray(flag_bytes_per_line)
        for gx in range(groups_per_line):
            prev_raw_line = raw_flags_by_line[y - 1] if y > 0 else None
            code = choose_flag(groups, gx, y, prev_raw_line)
            code_counts[code] += 1

            if code == 0:
                pixel_data += pack_group(groups[y][gx])

            xb = gx // 2
            if gx & 1:
                line_flags[xb] |= code & 0x0F
            else:
                line_flags[xb] |= (code & 0x0F) << 4

        raw_flags_by_line.append(line_flags)

    # 縦XORを取り、FlagA/FlagBへ圧縮する。
    flag_a = bytearray()
    flag_b = bytearray()
    bit_mask = 0x80
    current_a = 0
    prev_line = bytearray(flag_bytes_per_line)

    for y in range(height):
        line = raw_flags_by_line[y]
        for xb in range(flag_bytes_per_line):
            diff = line[xb] ^ prev_line[xb]
            if diff != 0:
                current_a |= bit_mask
                flag_b.append(diff)

            bit_mask >>= 1
            if bit_mask == 0:
                flag_a.append(current_a)
                current_a = 0
                bit_mask = 0x80

        prev_line = bytearray(line)

    if bit_mask != 0x80:
        flag_a.append(current_a)

    # 偶数境界へ合わせる。
    if len(flag_a) & 1:
        flag_a.append(0)
    if len(flag_b) & 1:
        flag_b.append(0)
    if len(pixel_data) & 1:
        pixel_data.append(0)

    info = {
        "groups_per_line": groups_per_line,
        "flag_bytes_per_line": flag_bytes_per_line,
        "code_counts": code_counts,
        "direct_groups": code_counts[0],
        "total_groups": groups_per_line * height,
    }
    return bytes(flag_a), bytes(flag_b), bytes(pixel_data), info


def encode_png_to_mag(src: Path, dst: Path) -> dict:
    img, palette_rgb = image_to_indexed_16(src)
    width, height = img.size

    if width <= 0 or height <= 0:
        raise ValueError("画像サイズが不正です")
    if width % 8 != 0:
        raise ValueError("MAGの基本に合わせるため、画像幅は8の倍数にしてください")

    comment = make_comment_area()
    bin_head = len(comment)
    palette_mag = build_mag_palette_grb(palette_rgb)

    flag_a, flag_b, pixel_data, comp_info = build_compressed_streams(img)

    flag_a_off = 32 + len(palette_mag)
    flag_b_off = flag_a_off + len(flag_a)
    flag_b_size = len(flag_b)
    pixel_off = flag_b_off + len(flag_b)
    pixel_size = len(pixel_data)

    header = bytearray(32)
    header[0] = 0x00
    header[1] = 0x00                  # machine code: PC98/その他
    header[2] = 0x00                  # machine flag
    header[3] = 0x00                  # 400line analog 16color
    header[4:6] = le16(0)
    header[6:8] = le16(0)
    header[8:10] = le16(width - 1)
    header[10:12] = le16(height - 1)
    header[12:16] = le32(flag_a_off)
    header[16:20] = le32(flag_b_off)
    header[20:24] = le32(flag_b_size)
    header[24:28] = le32(pixel_off)
    header[28:32] = le32(pixel_size)

    dst.write_bytes(comment + bytes(header) + palette_mag + flag_a + flag_b + pixel_data)

    uncompressed_pixel_size = comp_info["total_groups"] * 2
    return {
        "width": width,
        "height": height,
        "bin_head": bin_head,
        "flag_a_off": flag_a_off,
        "flag_a_size": len(flag_a),
        "flag_b_off": flag_b_off,
        "flag_b_size": flag_b_size,
        "pixel_off": pixel_off,
        "pixel_size": pixel_size,
        "file_size": dst.stat().st_size,
        "uncompressed_pixel_size": uncompressed_pixel_size,
        **comp_info,
    }


def print_summary(src: Path, dst: Path, info: dict) -> None:
    print(f"input : {src}")
    print(f"output: {dst}")
    print(f"size  : {info['width']}x{info['height']}")
    print(f"FlagA : offset={info['flag_a_off']} size={info['flag_a_size']}")
    print(f"FlagB : offset={info['flag_b_off']} size={info['flag_b_size']}")
    print(f"Pixel : offset={info['pixel_off']} size={info['pixel_size']}")
    print(f"file  : {info['file_size']} bytes")
    print(f"direct groups: {info['direct_groups']} / {info['total_groups']}")
    print(f"pixel shrink : {info['pixel_size']} / {info['uncompressed_pixel_size']} bytes")
    print("code counts:")
    for code, count in enumerate(info["code_counts"]):
        if count:
            pct = count * 100.0 / info["total_groups"]
            print(f"  code {code:2d}: {count:6d} ({pct:5.2f}%)")


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: python 002.png_to_mag.py input.png output.MAG")
        return 1

    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    info = encode_png_to_mag(src, dst)
    print_summary(src, dst, info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
