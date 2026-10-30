"""The paper figures' shared look: a headless matplotlib, one validated palette and one colormap.

Categorical colours follow a colourblind-checked order (blue, orange, aqua, yellow). The three
models that solve anything take the first three slots, which pass every check for all pairs; the
four core families take all four, with a legend. Magnitude (solve rate, stage reach) uses one blue
ramp, light to dark. Chrome (grid, axes, labels) stays recessive so the data carries the ink.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 - the backend must be chosen first
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
BLUE_LIGHT, BLUE_DEEP = "#9ec5f4", "#184f95"
INK = "#0b0b0b"      # primary text
INK2 = "#52514e"     # secondary text: annotations, tick labels
MUTED = "#898781"    # footnotes
GRID = "#e1e0d9"     # hairline gridlines
AXIS = "#c3c2b7"     # baseline and axis rules
SURFACE = "#ffffff"
CENSORED = "#e6e4df"       # policy-censored cells, hatched
CENSORED_INK = "#52514e"
GOOD, WARNING, CRITICAL = "#0ca30c", "#fab219", "#d03b3b"  # status colours for the cost regimes

NOTE = {"fontsize": 7.8, "color": MUTED}  # figure footnotes
CALLOUT = {"boxstyle": "round,pad=0.3", "fc": "#f3f7fc", "ec": BLUE_LIGHT}

MODEL_COLOR = {"Grok-4.7": BLUE, "Opus-5.5": ORANGE, "DeepSeek-V4-Pro": AQUA}
FAMILY_COLOR = {"pwcheck": BLUE, "vm": ORANGE, "hashid": AQUA, "xform": YELLOW}

# One hue, light to dark, for solve rate and stage reach; the lightest step reads as near zero.
SOLVE_CMAP = LinearSegmentedColormap.from_list("solve", ["#f3f7fc", BLUE_LIGHT, "#3987e5", BLUE_DEEP])

# Older names, kept for the supplementary scripts.
TEAL, DEEP, AMBER, RED, STONE = AQUA, BLUE_DEEP, ORANGE, CRITICAL, MUTED

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.labelsize": 10, "xtick.labelsize": 9, "ytick.labelsize": 9,
    "legend.fontsize": 9.5, "legend.frameon": False,
    "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "text.color": INK,
    "grid.color": GRID, "grid.linewidth": 0.8, "grid.linestyle": "-",
    "axes.spines.top": False, "axes.spines.right": False,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "pdf.fonttype": 42, "ps.fonttype": 42,  # embed TrueType, not Type 3, so the paper PDF passes arXiv checks
})


def despine(ax: plt.Axes) -> None:
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)


def save(fig: plt.Figure, png: Path) -> None:
    """Writes the figure as a PNG (for the Markdown reports) and as a vector PDF (for the paper)."""
    fig.savefig(png, dpi=200, bbox_inches="tight")
    fig.savefig(png.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)


__all__ = ["AMBER", "AQUA", "AXIS", "BLUE", "BLUE_DEEP", "BLUE_LIGHT", "CALLOUT", "CENSORED", "CENSORED_INK",
           "CRITICAL", "DEEP", "FAMILY_COLOR", "GOOD", "GRID", "INK", "INK2", "MODEL_COLOR", "MUTED", "NOTE",
           "ORANGE", "RED", "SOLVE_CMAP", "STONE", "SURFACE", "TEAL", "WARNING", "YELLOW", "despine", "plt",
           "save"]
