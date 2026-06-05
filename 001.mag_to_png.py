#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mag_to_png.py
MAKI02(MAG) 16色画像をPNGへ変換する最小デコーダーです。

使い方:
    python mag_to_png.py input.MAG
    python mag_to_png.py input.MAG output.png

必要:
    pip install pillow
"""

import sys
from pathlib import Path
from PIL import Image


DELTA_X = [0, 1, 2, 4, 0, 1, 0, 1, 2, 0, 1, 2, 0, 1, 2, 0]
DELTA_Y = [0, 0, 0, 0, 1, 1, 2, 2, 2, 4, 4, 4, 8, 8, 8, 16]


def u16le(data: bytes, off: int) -> int:
    return data[off] | (data[off + 1] << 8)


def u32le(data: bytes, off: int) -> int:
    return (
        data[off]
        | (data[off + 1] << 8)
        | (data[off + 2] << 16)
        | (data[off + 3] << 24)
    )


class BitReader:
    """Flag A を MSB first で1bitずつ読む"""

    def __init__(self, data: bytes):
        self.data = data
        self.byte_pos = 0
        self.bit_pos = 0

    def read_bit(self) -> int:
        if self.byte_pos >= len(self.data):
            return 0

        value = (self.data[self.byte_pos] >> (7 - self.bit_pos)) & 1

        self.bit_pos += 1
        if self.bit_pos >= 8:
            self.bit_pos = 0
            self.byte_pos += 1

        return value


def read_mag_header(data: bytes) -> dict:
    if data[:6] != b"MAKI02":
        raise ValueError("MAKI02形式ではありません")

    marker = data.find(b"\x1a")
    if marker < 0:
        raise ValueError("MAGヘッダ終端 0x1A が見つかりません")

    bin_head = marker + 1

    x0 = u16le(data, bin_head + 4)
    y0 = u16le(data, bin_head + 6)
    x1 = u16le(data, bin_head + 8)
    y1 = u16le(data, bin_head + 10)

    flag_a_off = u32le(data, bin_head + 12)
    flag_b_off = u32le(data, bin_head + 16)
    flag_b_size = u32le(data, bin_head + 20)
    pixel_off = u32le(data, bin_head + 24)
    pixel_size = u32le(data, bin_head + 28)

    width = x1 - x0 + 1
    height = y1 - y0 + 1

    # 今回は PC-98 640x400 16色MAGを主対象にする
    if width <= 0 or height <= 0:
        raise ValueError("画像サイズが不正です")

    return {
        "bin_head": bin_head,
        "machine_code": data[bin_head + 1],
        "screen_mode": data[bin_head + 2],
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "width": width,
        "height": height,
        "flag_a_off": flag_a_off,
        "flag_b_off": flag_b_off,
        "flag_b_size": flag_b_size,
        "pixel_off": pixel_off,
        "pixel_size": pixel_size,
    }


def read_palette_16(data: bytes, bin_head: int):
    palette = []
    pal_off = bin_head + 32

    for i in range(16):
        g = data[pal_off + i * 3 + 0]
        r = data[pal_off + i * 3 + 1]
        b = data[pal_off + i * 3 + 2]

        g = (g >> 4) * 17
        r = (r >> 4) * 17
        b = (b >> 4) * 17

        palette.append((r, g, b))

    return palette


def decode_mag_16(data: bytes) -> tuple[Image.Image, dict]:
    h = read_mag_header(data)

    width = h["width"]
    height = h["height"]
    bin_head = h["bin_head"]

    flag_a_abs = bin_head + h["flag_a_off"]
    flag_b_abs = bin_head + h["flag_b_off"]
    pixel_abs = bin_head + h["pixel_off"]

    flag_a_size = h["flag_b_off"] - h["flag_a_off"]
    flag_a = data[flag_a_abs : flag_a_abs + flag_a_size]
    flag_b = data[flag_b_abs : flag_b_abs + h["flag_b_size"]]
    pixel_data = data[pixel_abs : pixel_abs + h["pixel_size"]]

    palette = read_palette_16(data, bin_head)

    groups_per_line = (width + 3) // 4          # 16色MAGは4dot単位
    flag_bytes_per_line = (width + 7) // 8     # Flag Aは8dot単位で1bit

    bit_reader = BitReader(flag_a)

    flag_line = [0] * flag_bytes_per_line
    groups = [
        [(0, 0, 0, 0) for _ in range(groups_per_line)]
        for _ in range(height)
    ]

    pixels = [[0] * width for _ in range(height)]

    flag_b_pos = 0
    pixel_pos = 0

    for y in range(height):
        for xb in range(flag_bytes_per_line):
            # Flag A が1なら、同じ位置のフラグバッファに Flag B を XOR
            if bit_reader.read_bit():
                if flag_b_pos >= len(flag_b):
                    raise ValueError("Flag B データが不足しています")
                flag_line[xb] ^= flag_b[flag_b_pos]
                flag_b_pos += 1

            flags = flag_line[xb]

            # 上位4bit → 左側4dot、下位4bit → 右側4dot
            for half, code in enumerate(((flags >> 4) & 0x0F, flags & 0x0F)):
                group_x = xb * 2 + half
                if group_x >= groups_per_line:
                    continue

                if code == 0:
                    # code 0 はピクセルデータから直接読む
                    if pixel_pos + 1 >= len(pixel_data):
                        raise ValueError("ピクセルデータが不足しています")

                    b0 = pixel_data[pixel_pos]
                    b1 = pixel_data[pixel_pos + 1]
                    pixel_pos += 2

                    group = (
                        (b0 >> 4) & 0x0F,
                        b0 & 0x0F,
                        (b1 >> 4) & 0x0F,
                        b1 & 0x0F,
                    )
                else:
                    # code 1〜15 は過去の4dotブロックからコピー
                    src_x = group_x - DELTA_X[code]
                    src_y = y - DELTA_Y[code]

                    if 0 <= src_y < height and 0 <= src_x < groups_per_line:
                        group = groups[src_y][src_x]
                    else:
                        group = (0, 0, 0, 0)

                groups[y][group_x] = group

                for i, color_index in enumerate(group):
                    x = group_x * 4 + i
                    if x < width:
                        pixels[y][x] = color_index

    img = Image.new("P", (width, height))
    img.putdata([c for row in pixels for c in row])

    pil_palette = []
    for r, g, b in palette:
        pil_palette.extend([r, g, b])
    pil_palette.extend([0] * (768 - len(pil_palette)))
    img.putpalette(pil_palette)

    h["flag_a_used"] = bit_reader.byte_pos
    h["flag_b_used"] = flag_b_pos
    h["pixel_used"] = pixel_pos

    return img, h


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python mag_to_png.py input.MAG [output.png]")
        return 1

    src = Path(sys.argv[1])
    if len(sys.argv) >= 3:
        dst = Path(sys.argv[2])
    else:
        dst = src.with_suffix(".png")

    data = src.read_bytes()
    img, info = decode_mag_16(data)
    img.save(dst)

    print(f"input : {src}")
    print(f"output: {dst}")
    print(f"size  : {info['width']}x{info['height']}")
    print(f"FlagA : offset={info['flag_a_off']} size={info['flag_b_off'] - info['flag_a_off']}")
    print(f"FlagB : offset={info['flag_b_off']} size={info['flag_b_size']} used={info['flag_b_used']}")
    print(f"Pixel : offset={info['pixel_off']} size={info['pixel_size']} used={info['pixel_used']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
