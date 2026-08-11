# lichen

**Bake a single universal installer executable for any PyPI-distributed tool.**

Lichen produces one file — `yourtool.com` — that runs natively on Windows, macOS
(Intel + Apple Silicon), Linux (glibc + musl), and the BSDs, unchanged. When a
user runs it, it bootstraps [uv](https://github.com/astral-sh/uv) if the machine
doesn't have it, then installs your tool with `uv tool install <package>`.

Like its namesake, lichen is a symbiosis of two organisms that together grow on
any substrate: a ~200 KB [Cosmopolitan](https://github.com/jart/cosmopolitan)
APE loader, and uv doing the real work of platform detection, Python
provisioning, wheel resolution, and PATH management.

## Usage

Tool author (any machine with uv — no compiler toolchain needed):

```bash
uvx lichen bake --package soldr -o soldr-setup.com
```

End user (any OS, one file, no prerequisites):

```bash
./soldr-setup.com          # installs uv if needed, then `uv tool install soldr`
```

## Why

Shipping a CLI tool to "any machine" today means N installers, or telling users
to install a package manager first. PyPI + uv already solve artifact hosting,
per-platform wheels, Python provisioning, and upgrades — the only missing piece
is a zero-prerequisite first step that works everywhere. That first step is the
only thing lichen builds. It deliberately contains **no** payload binaries: the
`.com` is a trampoline, not a fat binary.

## How it works

1. The output is an [Actually Portable Executable](https://justine.lol/apeloader/):
   a polyglot file that is simultaneously a Windows PE, a `#!/bin/sh` script,
   and carrier of embedded ELF/Mach-O images for x86_64 + aarch64.
2. On first run the loader checks for `uv` on `PATH` (and its default install
   location). If missing, it invokes the official uv installer via tools every
   OS already has: `curl … | sh` (fallback `wget`) on Unix,
   `powershell -c "irm … | iex"` on Windows. No TLS stack in the loader.
3. It then spawns `uv tool install <package>`, waits, and forwards the exit
   code. Re-runs are idempotent.
4. The baked-in defaults (package name, optional uv version pin) live as a tiny
   ZIP entry inside the executable — APE files are valid ZIP archives and
   Cosmopolitan exposes entries under `/zip/` at runtime. Configuration only;
   never payloads.

The loader is a few hundred lines of C++ compiled with `cosmoc++` from the
[clang-tool-chain](https://pypi.org/project/clang-tool-chain/) PyPI package, so
`lichen bake` is pure Python orchestration — portable, unit-testable, and free
of host-toolchain requirements.

## Background: Cosmopolitan, APE, and the toolchain

Primer for implementers — everything lichen builds on:

- **[Cosmopolitan Libc](https://github.com/jart/cosmopolitan)** is a C library
  that makes C/C++ "build-once run-anywhere": one compiled artifact runs
  natively on Linux, Windows, macOS, and the BSDs, on both x86_64 and aarch64.
- **APE (Actually Portable Executable)** is its output format — a polyglot
  file whose first bytes are simultaneously a valid Windows PE (`MZ`), a POSIX
  `#!/bin/sh` script, and a carrier for embedded ELF/Mach-O program images.
  Windows executes it directly; Unix shells bootstrap it through a ~10 KB
  `ape` loader (self-extracted to `$TMPDIR/ape` when not installed); macOS ARM
  compiles that loader locally on first use. `--assimilate` converts a copy
  in-place to a plain native binary.
- **Every APE is also a valid ZIP archive.** Files zipped onto the end of the
  executable appear at runtime under the synthetic `/zip/…` path via normal
  `open()`/`read()` (transparent deflate). Lichen uses this for one small
  config entry — never for payload binaries (Cosmopolitan cannot `exec()`
  directly from `/zip/`, and lichen doesn't need to).
- **cosmocc / cosmoc++** is the GCC+Clang-based cross toolchain that emits
  APE binaries (`bin/cosmoc++ -o hello hello.cpp` → runs everywhere;
  `-mtiny` for ~180 KB output). Normally downloaded from
  [cosmo.zip](https://cosmo.zip/pub/cosmocc/); lichen instead consumes it via
  the [clang-tool-chain](https://pypi.org/project/clang-tool-chain/) PyPI
  package, which bundles cosmocc 4.0.2 with `clang-tool-chain-cosmocc` /
  `clang-tool-chain-cosmocpp` entry points — so the whole build runs under
  `uv` with zero host-toolchain setup:

  ```bash
  uv run --with clang-tool-chain clang-tool-chain-cosmocpp loader/loader.cpp -o lichen-loader.com
  ```

- **[uv](https://github.com/astral-sh/uv)** is the other symbiont: a single
  static binary (glibc, musl, mac universal, Windows builds all published)
  that resolves wheels, provisions managed CPython when the host has none,
  and manages tool installs (`uv tool install`) and PATH shims. Prior art for
  the extract-and-delegate pattern:
  [llamafile](https://github.com/mozilla-ai/llamafile), which extracts
  `/zip/` assets to `~/.llamafile/v/<version>/`.

See [DESIGN.md](DESIGN.md) for the full loader runtime contract, builder
pipeline, platform caveats, decisions, and test strategy. Design lineage:
[soldr#2460](https://github.com/zackees/soldr/issues/2460).

## Status

Design phase. Implementation tracked in
[issue #1](https://github.com/zackees/lichen/issues/1).
