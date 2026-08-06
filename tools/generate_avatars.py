"""Generates the default avatar gallery as SVG mascots.

No AI image-generation tool is wired up yet, so this produces a small set
of distinct programmatic placeholder avatars (varied color/feature
combinations) for the player picker. Swap in real generated art later by
dropping same-named files into frontend/public/avatars/, or point this
script at an image-generation API and keep the same output paths.

Run: python tools/generate_avatars.py
Keep PALETTE's length in sync with AVATAR_COUNT in frontend/src/avatars.js.
"""
from __future__ import annotations

import pathlib

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "frontend" / "public" / "avatars"

# (background, accent/outline, ) - eyes are always a white sclera + dark pupil
# so they read clearly against any background.
PALETTE = [
    ("#F87171", "#7F1D1D"),
    ("#FB923C", "#7C2D12"),
    ("#FBBF24", "#78350F"),
    ("#A3E635", "#365314"),
    ("#34D399", "#065F46"),
    ("#22D3EE", "#164E63"),
    ("#60A5FA", "#1E3A8A"),
    ("#818CF8", "#312E81"),
    ("#C084FC", "#4C1D95"),
    ("#F472B6", "#831843"),
    ("#FCA5A5", "#7F1D1D"),
    ("#5EEAD4", "#134E4A"),
]

TOPPERS = ["round", "horn", "antenna", "none"]
MOUTHS = ["smile", "flat", "open"]
PUPIL = "#1c212c"


def topper_svg(kind: str, accent: str) -> str:
    if kind == "round":
        return f'<circle cx="30" cy="22" r="9" fill="{accent}"/><circle cx="70" cy="22" r="9" fill="{accent}"/>'
    if kind == "horn":
        return f'<path d="M30 20 L20 2 L38 14 Z" fill="{accent}"/><path d="M70 20 L80 2 L62 14 Z" fill="{accent}"/>'
    if kind == "antenna":
        return f'<line x1="50" y1="18" x2="50" y2="2" stroke="{accent}" stroke-width="4"/><circle cx="50" cy="0" r="6" fill="{accent}"/>'
    return ""


def mouth_svg(kind: str, accent: str) -> str:
    if kind == "smile":
        return f'<path d="M40 62 Q50 74 60 62" stroke="{accent}" stroke-width="4" fill="none" stroke-linecap="round"/>'
    if kind == "flat":
        return f'<line x1="40" y1="64" x2="60" y2="64" stroke="{accent}" stroke-width="4" stroke-linecap="round"/>'
    return f'<ellipse cx="50" cy="66" rx="8" ry="6" fill="{accent}"/>'


def build_svg(bg: str, accent: str, topper: str, mouth: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <circle cx="50" cy="50" r="48" fill="{bg}"/>
  {topper_svg(topper, accent)}
  <circle cx="50" cy="46" r="30" fill="{bg}" stroke="{accent}" stroke-width="3"/>
  <circle cx="38" cy="42" r="6" fill="#ffffff"/>
  <circle cx="62" cy="42" r="6" fill="#ffffff"/>
  <circle cx="39.5" cy="42" r="2.4" fill="{PUPIL}"/>
  <circle cx="63.5" cy="42" r="2.4" fill="{PUPIL}"/>
  {mouth_svg(mouth, accent)}
</svg>'''


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i, (bg, accent) in enumerate(PALETTE, start=1):
        topper = TOPPERS[(i - 1) % len(TOPPERS)]
        mouth = MOUTHS[(i - 1) // len(TOPPERS) % len(MOUTHS)]
        svg = build_svg(bg, accent, topper, mouth)
        (OUT_DIR / f"avatar-{i:02d}.svg").write_text(svg, encoding="utf-8")
    print(f"wrote {len(PALETTE)} avatars to {OUT_DIR}")


if __name__ == "__main__":
    main()
