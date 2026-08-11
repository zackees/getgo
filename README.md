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

See [DESIGN.md](DESIGN.md) for the full design, platform caveats, and test
strategy. Design lineage: [soldr#2460](https://github.com/zackees/soldr/issues/2460).

## Status

Design phase. Implementation tracked in
[issue #1](https://github.com/zackees/lichen/issues/1).
