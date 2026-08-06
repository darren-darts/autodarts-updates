"""Generate the app icon: frontend/public/favicon.ico (+ a PNG for the web).

A dartboard, drawn from the same geometry the app uses everywhere else, so
the icon cannot drift away from what the app actually displays.

Written by hand rather than pulled from an icon set for the same reason the
avatars and game art are programmatic: no image-generation tool is wired up
here, and a generated file that regenerates identically is easier to keep
than a binary someone has to remember how to remake.

ICO is assembled directly - a 6-byte header, one 16-byte directory entry per
size, then PNG payloads. Windows has accepted PNG-in-ICO since Vista, which
avoids writing a BMP encoder for no benefit.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

OUT_ICO = ROOT / "frontend" / "public" / "favicon.ico"
OUT_PNG = ROOT / "frontend" / "public" / "favicon.png"

SIZES = (16, 32, 48, 64, 128, 256)

# BGR, matching a real board rather than a stylised one.
CREAM = (196, 222, 233)
BLACK = (24, 24, 26)
RED = (40, 40, 200)
GREEN = (70, 160, 40)
WIRE = (150, 150, 150)

SEGMENTS = [20, 1, 18, 4, 13, 6, 10, 15, 2, 17, 3, 19, 7, 16, 8, 11, 14, 9, 12, 5]


def draw(size: int) -> np.ndarray:
    """Render the board at `size`x`size` with an alpha channel."""
    scale = 8  # supersample, then downscale - cheap, effective antialiasing
    dimension = size * scale
    centre = dimension / 2.0
    image = np.zeros((dimension, dimension, 3), dtype=np.uint8)
    alpha = np.zeros((dimension, dimension), dtype=np.uint8)

    radius = dimension * 0.48
    # Proportions from the real board (double ring at 170mm of a 225.5mm face).
    r_double_out = radius * 0.985
    r_double_in = radius * 0.92
    r_treble_out = radius * 0.60
    r_treble_in = radius * 0.545
    r_outer_bull = radius * 0.115
    r_inner_bull = radius * 0.046

    cv2.circle(image, (int(centre), int(centre)), int(radius), BLACK, -1, cv2.LINE_AA)
    cv2.circle(alpha, (int(centre), int(centre)), int(radius), 255, -1, cv2.LINE_AA)

    step = 360.0 / 20.0
    for index, _number in enumerate(SEGMENTS):
        # Segment 20 straddles vertical, so each wedge is offset by half a step.
        start = index * step - 90 - step / 2.0
        end = start + step
        light = index % 2 == 0
        bed = CREAM if light else BLACK
        double = GREEN if light else RED
        treble = GREEN if light else RED

        box = ((centre, centre), (r_double_out * 2, r_double_out * 2), 0)
        for outer, inner, colour in (
            (r_double_out, r_double_in, double),
            (r_double_in, r_treble_out, bed),
            (r_treble_out, r_treble_in, treble),
            (r_treble_in, r_outer_bull, bed),
        ):
            points = wedge(centre, inner, outer, start, end)
            cv2.fillPoly(image, [points], colour, cv2.LINE_AA)

    cv2.circle(image, (int(centre), int(centre)), int(r_outer_bull), GREEN, -1, cv2.LINE_AA)
    cv2.circle(image, (int(centre), int(centre)), int(r_inner_bull), RED, -1, cv2.LINE_AA)

    ring = max(1, int(dimension * 0.004))
    for r in (r_double_out, r_double_in, r_treble_out, r_treble_in, r_outer_bull):
        cv2.circle(image, (int(centre), int(centre)), int(r), WIRE, ring, cv2.LINE_AA)

    merged = cv2.merge([*cv2.split(image), alpha])
    return cv2.resize(merged, (size, size), interpolation=cv2.INTER_AREA)


def wedge(centre: float, inner: float, outer: float, start: float, end: float) -> np.ndarray:
    angles = np.linspace(np.radians(start), np.radians(end), 12)
    outer_pts = [(centre + outer * np.cos(a), centre + outer * np.sin(a)) for a in angles]
    inner_pts = [(centre + inner * np.cos(a), centre + inner * np.sin(a)) for a in angles[::-1]]
    return np.array(outer_pts + inner_pts, dtype=np.int32)


def build_ico(images: list[np.ndarray]) -> bytes:
    encoded = []
    for image in images:
        ok, buffer = cv2.imencode(".png", image)
        if not ok:
            raise SystemExit("PNG encoding failed")
        encoded.append(buffer.tobytes())

    # ICONDIR: reserved, type 1 (icon), image count.
    header = struct.pack("<HHH", 0, 1, len(encoded))
    offset = len(header) + 16 * len(encoded)
    entries, payloads = b"", b""
    for image, data in zip(images, encoded):
        size = image.shape[0]
        # 256 is stored as 0 - the field is one byte and 256 does not fit.
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
        )
        payloads += data
        offset += len(data)
    return header + entries + payloads


def main() -> None:
    images = [draw(size) for size in SIZES]
    OUT_ICO.parent.mkdir(parents=True, exist_ok=True)
    OUT_ICO.write_bytes(build_ico(images))
    cv2.imwrite(str(OUT_PNG), draw(256))
    print(f"wrote {OUT_ICO} ({OUT_ICO.stat().st_size / 1024:.0f} KB, {len(SIZES)} sizes)")
    print(f"wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
