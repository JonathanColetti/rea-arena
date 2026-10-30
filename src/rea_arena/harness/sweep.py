"""Resumable, cost-capped sweeps of agent episodes over a priced chat API (OpenRouter).

Each (model, arm, cell) is one episode in a fresh Docker sandbox, scored by the real oracle,
and its full trajectory is written to ``<out>/<model>/<arm>/<task_id>.json``. A sweep is
**resumable** (an existing trajectory is skipped) and **cost-capped**: every episode's token
usage is priced and appended to ``<out>/ledger.jsonl``, and once the cap is reached no new
episode is launched, including ones already queued.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from rea_arena.gym import ReverseEnv, TaskRequest
from rea_arena.harness.agent import LoopConfig, run_episode
from rea_arena.harness.arms import ARMS
from rea_arena.harness.llm import OpenAIChat
from rea_arena.tasks.benchmark import Cell

OPENROUTER = "https://openrouter.ai/api/v1"
DEFAULT_MAX_TOKENS = 2048

Price = tuple[float, float]  # USD per prompt token, per completion token


def slug(model: str) -> str:
    """A model id as a directory name."""
    return model.replace("/", "__")


def fetch_prices(api_key: str, fallback: Mapping[str, Price]) -> dict[str, Price]:
    """Live per-token prices for the models in ``fallback``, which is used for any the
    provider does not list (or for all of them if the fetch fails)."""
    prices = dict(fallback)
    try:
        req = urllib.request.Request(f"{OPENROUTER}/models", headers={"Authorization": f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=30) as fh:  # noqa: S310 - fixed OpenRouter URL
            live = {m["id"]: m["pricing"] for m in json.load(fh)["data"]}
    except Exception as exc:  # noqa: BLE001 - pricing is best-effort; fall back to the table
        print(f"[price] live fetch failed ({exc}); using fallback table")
        return prices
    for model in fallback:
        if model in live:
            prices[model] = (float(live[model]["prompt"]), float(live[model]["completion"]))
    return prices


class Ledger:
    """Thread-safe running cost, logged to ``ledger.jsonl``, against a global cap. A resumed
    sweep reloads the log, so the cap covers every run into the same directory."""

    def __init__(self, out: Path, prices: Mapping[str, Price], cap: float):
        self.prices = prices
        self.cap = cap
        self.path = out / "ledger.jsonl"
        self.spent = 0.0
        self.by_model: dict[str, float] = {}
        self._lock = threading.Lock()
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._add(row["model"], row.get("cost", 0.0))

    def _add(self, model: str, cost: float) -> None:
        self.spent += cost
        self.by_model[model] = self.by_model.get(model, 0.0) + cost

    def cost_of(self, model: str, usage: dict) -> float:
        pin, pout = self.prices.get(model, (0.0, 0.0))
        return int(usage.get("prompt_tokens", 0)) * pin + int(usage.get("completion_tokens", 0)) * pout

    def record(self, row: dict) -> float:
        """Logs one episode's row (which carries its ``cost``); returns the total spent."""
        with self._lock:
            self._add(row["model"], row["cost"])
            with open(self.path, "a") as fh:
                fh.write(json.dumps(row) + "\n")
            return self.spent

    def over_cap(self) -> bool:
        with self._lock:
            return self.spent >= self.cap


@dataclass
class Sweep:
    out: Path
    ledger: Ledger
    api_key: str
    loop: LoopConfig = field(default_factory=LoopConfig)
    max_tokens: Mapping[str, int] = field(default_factory=dict)  # per model; else DEFAULT_MAX_TOKENS
    workers: int = 4

    def path(self, model: str, arm: str, cell: Cell) -> Path:
        return self.out / slug(model) / arm / f"{cell.task_id}.json"

    def todo(self, model: str, arm: str, cells: list[Cell]) -> list[Cell]:
        return [c for c in cells if not self.path(model, arm, c).exists()]

    def run(self, model: str, arm: str, cells: list[Cell]) -> None:
        """Runs every cell not already done, in parallel, until the ledger hits its cap."""
        todo = self.todo(model, arm, cells)
        print(f"\n== {model} [{arm}] : {len(todo)}/{len(cells)} to run "
              f"(model spent ${self.ledger.by_model.get(model, 0.0):.2f}) ==")
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(self.episode, model, arm, cell) for cell in todo]
            for _ in as_completed(futures):
                if self.ledger.over_cap():
                    print(f"[cap] reached ${self.ledger.spent:.2f} mid-model; cancelling queued episodes")
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

    def episode(self, model: str, arm_name: str, cell: Cell) -> dict | None:
        """Runs one episode and logs its cost; None if it was done already or the cap is hit."""
        dest = self.path(model, arm_name, cell)
        if dest.exists() or self.ledger.over_cap():
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        arm = ARMS[arm_name]
        chat = OpenAIChat(base_url=OPENROUTER, model=model, api_key=self.api_key, temperature=0.2,
                          max_tokens=self.max_tokens.get(model, DEFAULT_MAX_TOKENS))
        task = TaskRequest(seed=cell.seed, family=cell.family, target=cell.target, level=cell.level)
        started = time.time()
        try:
            with ReverseEnv(sandbox="docker", tools=arm.toolset) as env:
                traj = run_episode(env, chat, arm, task=task, loop=self.loop)
        except Exception as exc:  # noqa: BLE001 - log and keep sweeping; one bad episode is not fatal
            dest.with_suffix(".error").write_text(f"{type(exc).__name__}: {exc}\n")
            return {"model": model, "arm": arm_name, "task": cell.task_id, "error": str(exc)[:200]}
        dest.write_text(traj.to_json())
        row = {"model": model, "arm": arm_name, "task": cell.task_id, "reward": traj.reward,
               "stop_reason": traj.stop_reason, "steps": traj.num_steps, "usage": traj.usage,
               "cost": self.ledger.cost_of(model, traj.usage), "seconds": round(time.time() - started, 1)}
        total = self.ledger.record(row)
        furthest = traj.detail.get("shaped", {}).get("furthest")
        print(f"  [{total:6.2f}] {model.split('/')[-1]:22} {arm_name:10} {cell.task_id:22} "
              f"r={traj.reward} {traj.stop_reason:11} {traj.num_steps:2}st ${row['cost']:.3f}"
              + (f"  @{furthest}" if furthest else ""))
        return row
