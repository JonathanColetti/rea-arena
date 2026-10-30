"""Adaptive difficulty, after RLVE: each (family, target type) cell has its own level, raised
when the rolling solve rate is high and lowered when it is low."""

from __future__ import annotations

import json
import random
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class LevelPolicy:
    """When a cell's level moves: after ``window`` results at the current level, up if more than
    ``raise_above`` of them were solved, down if fewer than ``lower_below`` were."""

    window: int = 16
    raise_above: float = 0.8
    lower_below: float = 0.2


@dataclass
class Cell:
    level: int = 1
    recent: deque = field(default_factory=deque)


class Curriculum:
    def __init__(self, families: list[str], target_types: list[str], *, policy: LevelPolicy | None = None,
                 start_level: int = 1, seed: int | None = None):
        self.policy = policy or LevelPolicy()
        self.cells = {(f, t): Cell(start_level, deque(maxlen=self.policy.window))
                      for f in families for t in target_types}
        self.rng = random.Random(seed)

    def next(self) -> tuple[str, str, int]:
        """Picks a cell uniformly and returns (family, target type, level)."""
        family, target = self.rng.choice(sorted(self.cells))
        return family, target, self.cells[family, target].level

    def record(self, family: str, target: str, level: int, reward: float) -> None:
        cell = self.cells.get((family, target))
        if cell is None or level != cell.level:
            return  # results at other levels (e.g. fixed-level episodes) do not move the curriculum
        cell.recent.append(reward)
        if len(cell.recent) < self.policy.window:
            return
        rate = sum(cell.recent) / len(cell.recent)
        if rate > self.policy.raise_above:
            cell.level += 1
            cell.recent.clear()
        elif rate < self.policy.lower_below and cell.level > 1:
            cell.level -= 1
            cell.recent.clear()

    def state(self) -> dict:
        return {f"{f}/{t}": {"level": c.level, "recent": list(c.recent)} for (f, t), c in self.cells.items()}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.state(), indent=2) + "\n")

    def load(self, path: Path) -> None:
        for key, value in json.loads(path.read_text()).items():
            family, target = key.split("/")
            if (family, target) in self.cells:
                self.cells[family, target] = Cell(value["level"], deque(value["recent"], maxlen=self.policy.window))
