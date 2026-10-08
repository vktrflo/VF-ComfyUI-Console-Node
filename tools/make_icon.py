#!/usr/bin/env python3
"""Generate icon.png for the Comfy Registry listing.

Pure stdlib (zlib + struct) so it runs anywhere Python does, with no Pillow
dependency added just to draw 400x400 pixels.

    python tools/make_icon.py

Design goal: stay legible at the ~64px the registry and ComfyUI-Manager render
it, so shapes are few, contrast is high, and the spiral is drawn with real
anti-aliasing via 3x3 supersampling rather than a soft alpha ramp.
"""

import math
import struct
import zlib
from pathlib import Path

SIZE = 400
OUT = Path(__file__).resolve().parent.parent / "icon.png"

BG = (18, 18, 21)
PANEL = (32, 32, 37)
TITLEBAR = (46, 46, 52)
BORDER = (73, 55, 139)        # ComfyUI registry purple
GREEN = (86, 156, 110)
GREY = (136, 136, 145)
WHITE = (236, 236, 240)
RED = (255, 107, 107)
AMBER = (255, 184, 77)
BLUE = (159, 199, 255)

SS = 3  # supersampling factor per axis


def lerp(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded(dx, dy, w, h, r):
    """Inside-test for a rounded rectangle at offset (dx, dy) from its origin."""
    if dx < 0 or dy < 0 or dx >= w or dy >= h:
        return False
    cx = min(max(dx, r), w - 1 - r)
    cy = min(max(dy, r), h - 1 - r)
    return (dx - cx) ** 2 + (dy - cy) ** 2 <= r * r


def swirl_strength(x, y, cx, cy, turns, half_width):
    """Two-armed log spiral, 0..1, anti-aliased by distance to the arm.

    The arm half-width is a fixed fraction of the angular period. Making it
    grow with radius instead merges the arms into concentric rings, which is
    what this looked like before.
    """
    dx, dy = x - cx, y - cy
    r = math.hypot(dx, dy)
    if r > 88:
        return 0.0
    # Bend the polar angle outward as radius grows -> spiral arms.
    theta = math.atan2(dy, dx) + (r / 15.0) * turns
    arm = (theta / math.pi) % 1.0          # 0..1, two arms per revolution
    d = abs(arm - 0.5) * 2.0              # 1 at the gap, 0 at an arm centre
    lateral = 1.0 - min(1.0, d / half_width)
    # Fade in at the very centre and out at the rim, so no hard dot remains.
    fade = min(1.0, r / 11.0) * (1.0 - r / 88.0)
    return max(0.0, min(1.0, lateral * fade * 1.45))


def build():
    # Window geometry
    px0, py0 = 40, 88
    pw, ph = SIZE - 80, SIZE - 168
    bar_h = 38
    radius = 24

    log_lines = [
        (GREEN, 196),
        (GREY, 120),
        (BLUE, 226),
        (AMBER, 168),
        (RED, 150),
    ]
    line_h = 26
    line_x = px0 + 26
    first_y = py0 + bar_h + 34

    # Spiral sits in the lower-right, overlapping the window body.
    scx, scy = SIZE - 116, SIZE - 104

    acc = [[(0, 0, 0) for _ in range(SIZE)] for _ in range(SIZE)]
    weight = [[0 for _ in range(SIZE)] for _ in range(SIZE)]

    for sy in range(SIZE):
        for sx in range(SIZE):
            r_sum = [0, 0, 0]
            hits = 0
            for oy in range(SS):
                for ox in range(SS):
                    x = sx + (ox + 0.5) / SS
                    y = sy + (oy + 0.5) / SS
                    dx, dy = x - px0, y - py0

                    if not rounded(dx, dy, pw, ph, radius):
                        continue

                    if dy < bar_h:
                        colour = TITLEBAR
                    else:
                        colour = PANEL

                    # Window border: inside edge only.
                    edge = (
                        rounded(dx - 2, dy - 2, pw + 4, ph + 4, radius + 2)
                        and not rounded(dx, dy, pw, ph, radius)
                    ) or dy in (0.0, bar_h - 0.5) or dx in (0.0, pw - 0.5)
                    if edge:
                        colour = BORDER

                    # Three window dots.
                    for i, dot in enumerate((RED, AMBER, GREEN)):
                        ddx, ddy = x - (px0 + 30 + i * 26), y - (py0 + bar_h / 2)
                        if ddx * ddx + ddy * ddy <= 36:
                            colour = dot

                    # Log lines: a dim timestamp stub then a coloured body.
                    idx = int((y - first_y) // line_h)
                    ly = y - (first_y + idx * line_h)
                    if 0 <= idx < len(log_lines) and -1 <= ly <= line_h + 1:
                        colour_w, length = log_lines[idx]
                        if 0 <= ly <= 6:
                            seg_start = line_x
                            seg_end = line_x + length
                            if seg_start <= x <= seg_end:
                                colour = GREY if x < line_x + 34 else colour_w

                    # Spiral overlay, composited over whatever is beneath.
                    v = swirl_strength(x, y, scx, scy, turns=2.4, half_width=0.34)
                    if v > 0.01:
                        colour = lerp(colour, WHITE, min(1.0, v * 1.25))

                    r_sum[0] += colour[0]
                    r_sum[1] += colour[1]
                    r_sum[2] += colour[2]
                    hits += 1

            if hits:
                acc[sy][sx] = (
                    round(r_sum[0] / hits),
                    round(r_sum[1] / hits),
                    round(r_sum[2] / hits),
                )
                weight[sy][sx] = hits / (SS * SS)

    # Composite the window over the background using coverage as alpha.
    out = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            w = weight[y][x]
            if w <= 0:
                row.append(BG)
            elif w >= 0.999:
                row.append(acc[y][x])
            else:
                row.append(lerp(BG, acc[y][x], w))
        out.append(row)
    return out


def write_png(path, pixels):
    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBB", *pixels[y][x]) for x in range(SIZE))
        for y in range(SIZE)
    )

    def chunk(tag, data):
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


if __name__ == "__main__":
    write_png(OUT, build())
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {SIZE}x{SIZE})")