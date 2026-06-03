#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
003.mag_palette_dump.py
MAKI02(MAG) の16色パレットをテキストとPNG見本に出力します。

使い方:
    python 008.mag_palette_dump.py input.MAG

出力:
    input_palette.txt
    input_palette.png

必要:
    pip install pillow
"""

from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def u16le(data: bytes, off: int) -> int:
    return data[off] | (data[off + 1] << 8)


def u32le(data: bytes, off: int) -> int:
    return (
        data[off]
        | (data[off + 1] << 8)
        | (data[off + 2] << 16)
        | (data[off + 3] << 24)
    )


def mag_level(v: int) -> int:
    """MAGの8bit保存値を、だいたいの0〜15階調へ戻す。"""
    if v <= 0:
        return 0
    return v >> 4


def find_binary_header(data: bytes) -> int:
    if data[:8] != b"MAKI02  ":
        raise ValueError("MAKI02形式ではありません。先頭8バイトが b'MAKI02  ' ではありません。")

    marker = data.find(b"\x1a")
    if marker < 0:
        raise ValueError("コメント領域終端の 0x1A が見つかりません。")

    return marker + 1


def read_header(data: bytes) -> dict:
    bin_head = find_binary_header(data)

    if bin_head + 32 > len(data):
        raise ValueError("32バイトのMAGヘッダを読めません。")

    x0 = u16le(data, bin_head + 4)
    y0 = u16le(data, bin_head + 6)
    x1 = u16le(data, bin_head + 8)
    y1 = u16le(data, bin_head + 10)

    return {
        "bin_head": bin_head,
        "machine_code": data[bin_head + 1],
        "machine_flag": data[bin_head + 2],
        "screen_mode": data[bin_head + 3],
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "width": x1 - x0 + 1,
        "height": y1 - y0 + 1,
        "flag_a_off": u32le(data, bin_head + 12),
        "flag_b_off": u32le(data, bin_head + 16),
        "flag_b_size": u32le(data, bin_head + 20),
        "pixel_off": u32le(data, bin_head + 24),
        "pixel_size": u32le(data, bin_head + 28),
    }


def read_palette(data: bytes, bin_head: int) -> list[dict]:
    """MAGのパレットは GRB 順。16色分を RGB に直して返す。"""
    pal_off = bin_head + 32
    pal_size = 16 * 3

    if pal_off + pal_size > len(data):
        raise ValueError("16色パレット48バイトを読めません。")

    colors = []
    for i in range(16):
        g = data[pal_off + i * 3 + 0]
        r = data[pal_off + i * 3 + 1]
        b = data[pal_off + i * 3 + 2]
        colors.append({
            "index": i,
            "r": r,
            "g": g,
            "b": b,
            "grb_raw": (g, r, b),
            "r4": mag_level(r),
            "g4": mag_level(g),
            "b4": mag_level(b),
            "hex": f"#{r:02X}{g:02X}{b:02X}",
        })
    return colors


def write_text(path: Path, src: Path, header: dict, colors: list[dict]) -> None:
    lines: list[str] = []
    lines.append(f"file: {src.name}")
    lines.append("format: MAKI02")
    lines.append(f"binary header offset: {header['bin_head']}")
    lines.append("")
    lines.append("header:")
    lines.append(f"  machine_code : {header['machine_code']}")
    lines.append(f"  machine_flag : 0x{header['machine_flag']:02X}")
    lines.append(f"  screen_mode  : 0x{header['screen_mode']:02X}")
    lines.append(f"  area         : ({header['x0']},{header['y0']}) - ({header['x1']},{header['y1']})")
    lines.append(f"  size         : {header['width']} x {header['height']}")
    lines.append("")
    lines.append("palette:")
    lines.append("No  R   G   B    R4 G4 B4    HEX       MAG raw(G,R,B)")
    lines.append("--  --- --- ---  -- -- --    -------   ------------")
    for c in colors:
        g_raw, r_raw, b_raw = c["grb_raw"]
        lines.append(
            f"{c['index']:2d}  "
            f"{c['r']:3d} {c['g']:3d} {c['b']:3d}  "
            f"{c['r4']:2d} {c['g4']:2d} {c['b4']:2d}    "
            f"{c['hex']}   "
            f"({g_raw:3d},{r_raw:3d},{b_raw:3d})"
        )
    lines.append("")
    lines.append("note:")
    lines.append("  MAG palette order in file is GRB, not RGB.")
    lines.append("  R4/G4/B4 are approximate 0-15 levels restored from the stored 8-bit values.")

    path.write_text("\n".join(lines), encoding="utf-8")


def text_color_for_bg(r: int, g: int, b: int) -> tuple[int, int, int]:
    # 簡易輝度。明るい色には黒文字、暗い色には白文字。
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return (0, 0, 0) if lum >= 150 else (255, 255, 255)


def write_preview(path: Path, colors: list[dict]) -> None:
    cell_w = 120
    cell_h = 70
    cols = 4
    rows = 4
    margin = 16

    w = cell_w * cols + margin * 2
    h = cell_h * rows + margin * 2
    img = Image.new("RGB", (w, h), (32, 32, 32))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 14)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
        font_small = ImageFont.load_default()

    for c in colors:
        i = c["index"]
        x = margin + (i % cols) * cell_w
        y = margin + (i // cols) * cell_h
        rgb = (c["r"], c["g"], c["b"])
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], fill=rgb)
        draw.rectangle([x, y, x + cell_w - 1, y + cell_h - 1], outline=(0, 0, 0))

        fg = text_color_for_bg(*rgb)
        draw.text((x + 8, y + 6), f"{i:02d}", fill=fg, font=font)
        draw.text((x + 8, y + 28), c["hex"], fill=fg, font=font_small)
        draw.text((x + 8, y + 46), f"{c['r4']:X}{c['g4']:X}{c['b4']:X}", fill=fg, font=font_small)

    img.save(path)


def dump_palette(src: Path, txt_path: Path | None, png_path: Path | None) -> tuple[Path, Path]:
    data = src.read_bytes()
    header = read_header(data)
    colors = read_palette(data, header["bin_head"])

    if txt_path is None:
        txt_path = src.with_name(src.stem + "_palette.txt")
    if png_path is None:
        png_path = src.with_name(src.stem + "_palette.png")

    write_text(txt_path, src, header, colors)
    write_preview(png_path, colors)

    return txt_path, png_path


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if len(argv) not in (1, 3):
        print("usage:")
        print("  python 003.mag_palette_dump.py input.MAG")
        print("  python 003.mag_palette_dump.py input.MAG output_palette.txt output_palette.png")
        return 1

    src = Path(argv[0])
    txt_path = Path(argv[1]) if len(argv) == 3 else None
    png_path = Path(argv[2]) if len(argv) == 3 else None

    txt_out, png_out = dump_palette(src, txt_path, png_path)

    print(f"wrote: {txt_out}")
    print(f"wrote: {png_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
