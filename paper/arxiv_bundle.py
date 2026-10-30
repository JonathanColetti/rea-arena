#!/usr/bin/env python3
"""Write the arXiv source bundle: paper.tex with its graphicspath rewritten to ``figures/``, plus
the four figures it includes, as build/dist/arxiv-src.zip. Nothing else is needed to compile it.

    uv run python paper/arxiv_bundle.py          # from the repo root
    (cd /tmp/x && unzip .../arxiv-src.zip && xelatex paper.tex && xelatex paper.tex)
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEX = ROOT / "paper" / "paper.tex"
OUT = ROOT / "build" / "dist" / "arxiv-src.zip"


def main() -> int:
    tex = TEX.read_text()
    paths = re.search(r"\\graphicspath\{(.*)\}", tex).group(1)
    dirs = [ROOT / "paper" / d for d in re.findall(r"\{([^{}]+)\}", paths)]
    figures = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex)
    bundled = tex.replace(f"\\graphicspath{{{paths}}}", "\\graphicspath{{figures/}}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("paper.tex", bundled)
        for name in figures:
            src = next(d / name for d in dirs if (d / name).exists())
            z.write(src, f"figures/{name}")
    print(f"wrote {OUT} with paper.tex and {len(figures)} figures: {', '.join(figures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
