"""Per-byte transform pipelines, shared by the ``xform`` and ``pwcheck`` families.

A pipeline is a list of ops applied to each input byte ``v`` at position ``i``. Every op is a
bijection on bytes (given the previous output byte for ``chain``), so an encoded secret has
exactly one preimage. Stateful ops carry an index ``k`` so a pipeline can hold several of them.

Each op kind is defined once, in :data:`OPS`, by four small functions: how to draw its
parameters, how to apply it in Python, how to emit it as C, and how to emit it as a line of a
standalone Python reimplementation. Adding an op means adding one :class:`Op` here.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from rea_arena.families.base import MASK32, c_bytes, rotl8


@dataclass
class State:
    """What a pipeline carries from byte to byte: each LCG's register (by op index ``k``) and
    the previous output byte (for ``chain``)."""

    lcg: dict[int, int] = field(default_factory=dict)
    prev: int = 0


@dataclass(frozen=True)
class Op:
    name: str
    stateful: bool
    draw: Callable[[random.Random, dict], None]
    apply: Callable[[dict, int, int, State], int]  # (op, v, i, state) -> v
    c_body: Callable[[dict], str]
    py_body: Callable[[dict], str]
    c_decl: Callable[[dict], str] = lambda _op: ""  # static tables (sbox)


def _draw_xor_add(rng: random.Random, op: dict) -> None:
    op["c"] = rng.randrange(1, 256)


def _draw_rotl(rng: random.Random, op: dict) -> None:
    op["r"] = rng.randint(1, 7)


def _draw_posxor(rng: random.Random, op: dict) -> None:
    op["m"], op["c"] = rng.randrange(1, 256, 2), rng.randrange(256)


def _draw_sbox(rng: random.Random, op: dict) -> None:
    table = list(range(256))
    rng.shuffle(table)
    op["table"] = table


def _draw_lcg(rng: random.Random, op: dict) -> None:
    op["seed"], op["b"] = rng.getrandbits(32), rng.getrandbits(32) | 1
    op["a"] = (rng.getrandbits(32) & ~3) | 1  # a = 1 mod 4: full-period LCG


def _draw_chain(rng: random.Random, op: dict) -> None:
    op["iv"] = rng.randrange(256)


def _apply_lcg(op: dict, v: int, _i: int, state: State) -> int:
    state.lcg[op["k"]] = (state.lcg[op["k"]] * op["a"] + op["b"]) & MASK32
    return v ^ (state.lcg[op["k"]] >> 24)


OPS: dict[str, Op] = {
    "xor": Op(
        "xor", False, _draw_xor_add,
        lambda op, v, _i, _s: v ^ op["c"],
        lambda op: f"v ^= {op['c']};",
        lambda op: f"v ^= {op['c']}",
    ),
    "add": Op(
        "add", False, _draw_xor_add,
        lambda op, v, _i, _s: (v + op["c"]) & 0xFF,
        lambda op: f"v = (u8)(v + {op['c']});",
        lambda op: f"v = (v + {op['c']}) & 0xFF",
    ),
    "rotl": Op(
        "rotl", False, _draw_rotl,
        lambda op, v, _i, _s: rotl8(v, op["r"]),
        lambda op: f"v = (u8)((v << {op['r']}) | (v >> {8 - op['r']}));",
        lambda op: f"v = ((v << {op['r']}) | (v >> {8 - op['r']})) & 0xFF",
    ),
    "not": Op(
        "not", False, lambda _rng, _op: None,
        lambda _op, v, _i, _s: v ^ 0xFF,
        lambda _op: "v = (u8)~v;",
        lambda _op: "v ^= 0xFF",
    ),
    "posxor": Op(
        "posxor", False, _draw_posxor,
        lambda op, v, i, _s: v ^ ((i * op["m"] + op["c"]) & 0xFF),
        lambda op: f"v ^= (u8)((u32)i * {op['m']}u + {op['c']}u);",
        lambda op: f"v ^= (i * {op['m']} + {op['c']}) & 0xFF",
    ),
    "sbox": Op(
        "sbox", True, _draw_sbox,
        lambda op, v, _i, _s: op["table"][v],
        lambda op: f"v = sbox{op['k']}[v];",
        lambda op: f"v = SBOX{op['k']}[v]",
        c_decl=lambda op: f"static const u8 sbox{op['k']}[256] = {{ {c_bytes(op['table'])} }};",
    ),
    "lcg": Op(
        "lcg", True, _draw_lcg, _apply_lcg,
        lambda op: f"s{op['k']} = s{op['k']} * 0x{op['a']:08x}u + 0x{op['b']:08x}u; v ^= (u8)(s{op['k']} >> 24);",
        lambda op: (f"s{op['k']} = (s{op['k']} * 0x{op['a']:08x} + 0x{op['b']:08x}) & 0xFFFFFFFF; "
                    f"v ^= s{op['k']} >> 24"),
    ),
    "chain": Op(
        "chain", True, _draw_chain,
        lambda _op, v, _i, state: v ^ state.prev,
        lambda _op: "v ^= prev;",
        lambda _op: "v ^= prev",
    ),
}

SIMPLE = [name for name, op in OPS.items() if not op.stateful]
STATEFUL = [name for name, op in OPS.items() if op.stateful]


def draw(rng: random.Random, n_ops: int) -> list[dict]:
    """Draws ``n_ops`` ops; stateful ops join the pool once the pipeline is long enough."""
    ops: list[dict] = []
    has_chain = False
    for k in range(n_ops):
        kinds = SIMPLE + (STATEFUL if n_ops >= 2 else [])
        if has_chain:
            kinds = [x for x in kinds if x != "chain"]
        kind = rng.choice(kinds)
        op: dict = {"op": kind, "k": k}
        OPS[kind].draw(rng, op)
        if kind == "chain":
            has_chain = True
        ops.append(op)
    return ops


def chain_iv(ops: list[dict]) -> int:
    return next((op["iv"] for op in ops if op["op"] == "chain"), 0)


def apply(ops: list[dict], data: bytes) -> bytes:
    state = State({op["k"]: op["seed"] for op in ops if op["op"] == "lcg"}, chain_iv(ops))
    out = bytearray()
    for i, byte in enumerate(data):
        v = byte
        for op in ops:
            v = OPS[op["op"]].apply(op, v, i, state)
        state.prev = v
        out.append(v)
    return bytes(out)


def c_decls(ops: list[dict]) -> str:
    """Static tables to declare before the function."""
    return "\n".join(d for d in (OPS[op["op"]].c_decl(op) for op in ops) if d)


def c_state(ops: list[dict]) -> str:
    """State variables to declare before the byte loop."""
    lines = [f"u32 s{op['k']} = 0x{op['seed']:08x}u;" for op in ops if op["op"] == "lcg"]
    lines.append(f"u8 prev = {chain_iv(ops)}; (void)prev;")
    return " ".join(lines)


def c_body(ops: list[dict]) -> str:
    """Statements transforming ``v`` at position ``i`` (an int)."""
    return "\n        ".join(OPS[op["op"]].c_body(op) for op in ops)


def py_program(ops: list[dict]) -> str:
    """A standalone Python script printing the pipeline output of stdin as hex."""
    tables = "".join(f"SBOX{op['k']} = {op['table']!r}\n" for op in ops if op["op"] == "sbox")
    state = "".join(f"s{op['k']} = 0x{op['seed']:08x}\n" for op in ops if op["op"] == "lcg")
    body = [OPS[op["op"]].py_body(op) for op in ops]
    lcg_names = [f"s{op['k']}" for op in ops if op["op"] == "lcg"]
    glob = f"    global {', '.join(lcg_names)}\n" if lcg_names else ""
    return (
        "import sys\n\n"
        f"{tables}{state}"
        f"prev = {chain_iv(ops)}\n\n\n"
        "def step(i, v):\n"
        f"{glob}"
        "    global prev\n"
        + "".join(f"    {line}\n" for line in body)
        + "    prev = v\n"
        "    return v\n\n\n"
        "data = sys.stdin.buffer.read()[:4096]\n"
        "print(bytes(step(i, v) for i, v in enumerate(data)).hex())\n"
    )
