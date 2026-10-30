"""Compile a family's C core into a native ELF or a WASM module.

Every target shares one ABI: the core defines `int run(int n)`, which reads `n` bytes from
`g_in` and writes its output to `g_out`, returning the output length. The native build wraps
it in a `main` that moves stdin/stdout; the WASM build exports `run`, `get_in` and `get_out`,
and `runtime/run_wasm.js` does the same job under node.
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rea_arena.config import RUNTIME_DIR, TARGET_TYPES

PRELUDE = r"""
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

#define IN_MAX 4096
#define OUT_MAX 16384

static u8 g_in[IN_MAX + 1];
static u8 g_out[OUT_MAX];
static int g_olen;

u8 *get_in(void) { return g_in; }
u8 *get_out(void) { return g_out; }

static void put_byte(u8 b) { if (g_olen < OUT_MAX) g_out[g_olen++] = b; }
static void put_str(const char *s) { while (*s) put_byte((u8)*s++); }
static void put_hex8(u8 v) {
    static const char digits[] = "0123456789abcdef";
    put_byte((u8)digits[v >> 4]);
    put_byte((u8)digits[v & 15]);
}
static void put_hex32(u32 v) { for (int i = 24; i >= 0; i -= 8) put_hex8((u8)(v >> i)); }

#ifdef RA_WASM
void *memset(void *d, int c, __SIZE_TYPE__ n) {
    volatile u8 *p = d;
    while (n--) *p++ = (u8)c;
    return d;
}
void *memcpy(void *d, const void *s, __SIZE_TYPE__ n) {
    volatile u8 *p = d;
    const u8 *q = s;
    while (n--) *p++ = *q++;
    return d;
}
#endif
"""

NATIVE_MAIN = r"""
int main(void) {
    int n = 0, r;
    while (n < IN_MAX && (r = (int)read(0, g_in + n, (unsigned)(IN_MAX - n))) > 0) n += r;
    int m = run(n);
    int off = 0;
    while (off < m && (r = (int)write(1, g_out + off, (unsigned)(m - off))) > 0) off += r;
    return 0;
}
"""

# Task images are ubuntu:24.04, so native targets may not need a newer glibc than this.
MAX_GLIBC = (2, 39)


@dataclass
class BuildConfig:
    target_type: str  # "elf" or "wasm"
    compiler: str
    opt: str
    strip: bool

    def describe(self) -> str:
        return f"{self.compiler} {self.opt} {'stripped' if self.strip else 'symbols'}"


def pick_config(rng: random.Random, target_type: str, level: int) -> BuildConfig:
    """Level 1: unoptimized with symbols. Level 2: optimized with symbols. 3+: optimized, stripped."""
    if target_type not in TARGET_TYPES:
        raise ValueError(f"unknown target type {target_type!r}")
    optimized = ["-O1", "-O2", "-O3", "-Os"] if target_type == "elf" else ["-O1", "-O2", "-Os"]
    compiler = rng.choice(["gcc", "clang"]) if target_type == "elf" else "clang"
    opt = "-O0" if level == 1 else rng.choice(optimized)
    return BuildConfig(target_type, compiler, opt, strip=level >= 3)


def find_wasm_ld() -> str:
    found = shutil.which("wasm-ld")
    if found:
        return found
    sysroot = subprocess.run(["rustc", "--print", "sysroot"], capture_output=True, text=True, check=False)
    if sysroot.returncode == 0:
        for lld in Path(sysroot.stdout.strip()).glob("lib/rustlib/*/bin/rust-lld"):
            return str(lld)
    raise RuntimeError("no WASM linker: install wasm-ld (lld) or a Rust toolchain (rust-lld)")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{proc.stderr}")


def _check_glibc(binary: Path) -> None:
    syms = subprocess.run(["objdump", "-T", str(binary)], capture_output=True, text=True, check=True).stdout
    versions = {tuple(int(x) for x in v.split(".")) for v in re.findall(r"GLIBC_(\d+(?:\.\d+)+)", syms)}
    if versions and max(versions) > MAX_GLIBC:
        raise RuntimeError(f"{binary} needs GLIBC {max(versions)}, newer than the task image")


def _build_elf(core_c: str, config: BuildConfig, work: Path, out_dir: Path) -> list[Path]:
    src = work / "target.c"
    src.write_text("#include <unistd.h>\n" + PRELUDE + core_c + NATIVE_MAIN)
    binary = out_dir / "target"
    cmd = [config.compiler, config.opt, "-w", "-o", str(binary), str(src)]
    if config.strip:
        cmd.append("-s")
    _run(cmd)
    _check_glibc(binary)
    return [binary]


def _build_wasm(core_c: str, config: BuildConfig, work: Path, out_dir: Path) -> list[Path]:
    src = work / "target.c"
    src.write_text(PRELUDE + core_c)
    obj = work / "target.o"
    _run(["clang", "--target=wasm32", config.opt, "-w", "-nostdlib", "-ffreestanding", "-fno-builtin",
          "-DRA_WASM", "-c", str(src), "-o", str(obj)])
    wasm = out_dir / "target.wasm"
    link = [find_wasm_ld()]
    if Path(link[0]).name == "rust-lld":
        link += ["-flavor", "wasm"]
    link += ["--no-entry", "--export=run", "--export=get_in", "--export=get_out", str(obj), "-o", str(wasm)]
    if config.strip:
        link.append("--strip-all")
    _run(link)
    runner = out_dir / "run_wasm.js"
    shutil.copy(RUNTIME_DIR / "run_wasm.js", runner)
    return [wasm, runner]


_BUILDERS = {"elf": _build_elf, "wasm": _build_wasm}


def build(core_c: str, config: BuildConfig, out_dir: Path) -> list[Path]:
    """Compile `core_c` into `out_dir`; return the target files to ship."""
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / ".build"
    work.mkdir(exist_ok=True)
    try:
        return _BUILDERS[config.target_type](core_c, config, work, out_dir)
    finally:
        shutil.rmtree(work, ignore_errors=True)
