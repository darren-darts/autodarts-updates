"""Slice the Mr vs Mrs chore artwork sheet into one PNG per chore.

The chore artwork arrives as a single square sheet of 12 tiles, 4 across and
3 down, in the same order as `CHORES` in backend/games/chores.py:

    laundry        washing_up   vacuuming   food_shopping
    bins           bathroom     cooking     dog_walk
    bedding        car_wash     gardening   kitchen

Run it once, whenever the sheet changes:

    backend/.venv/Scripts/python.exe tools/slice_chore_images.py path/to/sheet.png

Output lands in frontend/public/chores/<id>.png, which the play screen looks
for at /chores/<id>.png. Nothing else needs changing - the chore card shows
the artwork if the file is there and falls back to the chore's emoji if it is
not, so the game works either way and simply looks better once this has run.

Asset generation has optional dependencies separate from the shipped backend:
install them with `pip install -r tools/requirements.txt`. --margin trims the
dark gutter some sheets carry between tiles.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "frontend" / "public" / "chores"

COLUMNS, ROWS = 4, 3

# Reading order across the sheet. Must match CHORES in backend/games/chores.py -
# these ids are what the frontend requests.
IDS = [
    "laundry", "washing_up", "vacuuming", "food_shopping",
    "bins", "bathroom", "cooking", "dog_walk",
    "bedding", "car_wash", "gardening", "kitchen",
]


def _display(path: Path) -> str:
    """Repo-relative when it can be - a custom --out may be anywhere at all."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def slice_sheet(sheet_path: Path, out_dir: Path, margin: int, size: int) -> int:
    sheet = cv2.imread(str(sheet_path), cv2.IMREAD_UNCHANGED)
    if sheet is None:
        print(f"Could not read {sheet_path}", file=sys.stderr)
        return 1

    height, width = sheet.shape[:2]
    tile_w, tile_h = width // COLUMNS, height // ROWS
    if tile_w == 0 or tile_h == 0:
        print(f"{sheet_path} is too small to be a {COLUMNS}x{ROWS} sheet", file=sys.stderr)
        return 1
    print(f"{sheet_path.name}: {width}x{height} -> {COLUMNS}x{ROWS} tiles of {tile_w}x{tile_h}")

    out_dir.mkdir(parents=True, exist_ok=True)
    for index, chore_id in enumerate(IDS):
        row, column = divmod(index, COLUMNS)
        top = row * tile_h + margin
        left = column * tile_w + margin
        tile = sheet[top:top + tile_h - 2 * margin, left:left + tile_w - 2 * margin]
        if size:
            # INTER_AREA is the right filter for downscaling - anything else
            # leaves these flat illustrations looking crunchy.
            tile = cv2.resize(tile, (size, size), interpolation=cv2.INTER_AREA)
        destination = out_dir / f"{chore_id}.png"
        if not cv2.imwrite(str(destination), tile):
            print(f"  failed to write {destination}", file=sys.stderr)
            return 1
        print(f"  {chore_id:<14} -> {_display(destination)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sheet", type=Path, help="the 4x3 chore artwork sheet")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help=f"output directory (default: {OUT_DIR})")
    parser.add_argument("--margin", type=int, default=6,
                        help="pixels to trim from each tile edge, to drop the gutter (default: 6)")
    parser.add_argument("--size", type=int, default=512,
                        help="square output size in pixels, 0 to keep the source size (default: 512)")
    args = parser.parse_args()

    if not args.sheet.is_file():
        print(f"No such file: {args.sheet}", file=sys.stderr)
        return 1
    return slice_sheet(args.sheet, args.out, max(0, args.margin), max(0, args.size))


if __name__ == "__main__":
    raise SystemExit(main())
