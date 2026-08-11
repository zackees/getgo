# getgo

**One file that installs your tool on any machine — works from the get-go.**

getgo bakes a single universal executable — `yourtool.com` — that runs natively
on Windows, macOS (Intel + Apple Silicon), and Linux (glibc + musl, x86_64 +
aarch64), unchanged. When a user runs it, it bootstraps
[uv](https://github.com/astral-sh/uv) if the machine doesn't have it, then
installs your tool with `uv tool install <package>`.

```bash
# Tool author — any machine with uv, no compiler toolchain:
uvx getgo bake --package soldr -o soldr-setup.com

# End user — any OS/arch, one file, zero prerequisites:
./soldr-setup.com
```

## Why

Shipping a CLI tool to "any machine" means N installers, or telling users to
install a package manager first. PyPI + uv already solve artifact hosting,
per-platform wheels, Python provisioning, and upgrades. The only missing piece
is a zero-prerequisite first step that works everywhere — that first step is
the *only* thing getgo builds. The `.com` is a trampoline, not a fat binary:
it contains no payloads, just enough logic to summon uv and delegate.

## Architecture

Three stages, each owned by the layer best at it:

```
 stage 0                    stage 1                     stage 2
 ┌─────────────────┐        ┌──────────────────┐        ┌──────────────────────┐
 │ yourtool.com    │        │ ensure uv        │        │ uv tool install pkg  │
 │ (APE loader,    │ ─────► │ (official        │ ─────► │ (wheel + managed     │
 │  ~200 KB, fat   │        │  installer, or   │        │  CPython if needed,  │
 │  x86_64+arm64)  │        │  already there)  │        │  PATH shim)          │
 └─────────────────┘        └──────────────────┘        └──────────────────────┘
```

### Stage 0 — the APE loader (getgo's code)

The output of `getgo bake` is an
[Actually Portable Executable](https://justine.lol/apeloader/): a polyglot
file that is simultaneously a Windows PE, a `#!/bin/sh` script, and a carrier
of embedded ELF/Mach-O images for **both x86_64 and aarch64** (woven by
`apelink`, so the same `.com` covers Intel and ARM). Written in a few hundred
lines of C++, compiled with `cosmoc++` from the
[clang-tool-chain](https://pypi.org/project/clang-tool-chain/) PyPI package —
so baking runs entirely under uv with no host toolchain.

Startup per platform:

| Platform | How the `.com` starts |
|---|---|
| Windows x64 | It *is* a native PE — direct execution. |
| Linux glibc/musl, BSDs | `/bin/sh` header execs `ape` from `$PATH`, else self-extracts the ~10 KB loader to `$TMPDIR/ape` and re-execs. Works under busybox `ash` (Alpine). |
| macOS Intel | Native path via the same shell bootstrap. |
| macOS ARM | Loader compiled from embedded source with local `cc` on first run (needs Xcode CLT) or found at `/usr/local/bin/ape`. |

Baked-in defaults (package name, optional uv version pin) are stored as a
single ZIP entry readable at `/zip/getgo.json` — APE files are valid ZIP
archives and Cosmopolitan exposes entries through ordinary `open()`/`read()`.

### Stage 1 — uv bootstrap, per target

The loader never links a TLS stack. If `uv` isn't on `PATH` (or at its default
install dir), it invokes the official installers via tools every OS ships,
and those installers do their own platform/arch/libc detection against uv's
prebuilt artifacts:

| Target | Trigger | uv artifact installed |
|---|---|---|
| linux **x64 glibc** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (fallback `wget -qO-`) | `uv-x86_64-unknown-linux-gnu` |
| linux **arm64 glibc** | same | `uv-aarch64-unknown-linux-gnu` |
| linux **x64 musl** (Alpine) | same — busybox `wget` suffices | `uv-x86_64-unknown-linux-musl` (static) |
| linux **arm64 musl** | same | `uv-aarch64-unknown-linux-musl` (static) |
| macOS x64 / arm64 | same | `uv-{x86_64,aarch64}-apple-darwin` |
| Windows x64 | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` | `uv-x86_64-pc-windows-msvc` |

uv's musl builds are fully static, so the same binary serves Alpine, Void,
and any other musl distro — no glibc compatibility question on the
bootstrap path.

### Stage 2 — delegation to uv

The loader spawns `uv tool install <package>`, waits, and forwards the exit
code (spawn/wait is the one shape that behaves identically everywhere —
Cosmopolitan's `execve` on Windows spawns rather than replaces). uv then:

- resolves the right wheel for the host (`win_amd64`, `macosx_*`,
  `manylinux_*_{x86_64,aarch64}`, `musllinux_*_{x86_64,aarch64}`),
- provisions a **managed CPython** if the host has no Python — including
  musl builds, so a stock Alpine container with no Python works,
- installs into an isolated env and links the executable into
  `~/.local/bin`, printing the PATH hint if that isn't on `PATH`.

`uv tool install` is used deliberately instead of `uv pip install --system`:
the latter requires a pre-existing system Python and fights PEP 668
externally-managed distros; the former needs neither.

**Requirement this places on your package:** it must publish wheels for the
platforms you care about — *including `musllinux` wheels* if Alpine users
matter to you. getgo can't conjure a wheel that doesn't exist.

## Repository layout (planned)

- `loader/` — the C++ APE loader (`cosmoc++`, `-mtiny`, fat x86_64+aarch64)
- `src/getgo/` — the Python builder CLI (`getgo bake`)
- `tests/` — unit tests (config ZIP append, uv detection, command mapping)
  plus the Alpine-musl Docker gate that is this repo's merge bar

## Background: Cosmopolitan, APE, and the toolchain

Primer for implementers — everything getgo builds on:

- **[Cosmopolitan Libc](https://github.com/jart/cosmopolitan)** makes C/C++
  "build-once run-anywhere": one artifact runs natively on Linux, Windows,
  macOS, and the BSDs, on x86_64 and aarch64.
- **APE** is its polyglot output format (`MZ` header for Windows, `#!/bin/sh`
  for Unix, embedded ELF/Mach-O images). `--assimilate` converts a copy
  in-place to a plain native binary.
- **Every APE is also a valid ZIP archive.** Entries appended to the
  executable appear at runtime under `/zip/…` with transparent decompression.
  getgo uses this for one config entry — never payloads (Cosmopolitan cannot
  `exec()` from `/zip/`, and getgo doesn't need to).
- **cosmocc / cosmoc++** is the GCC+Clang cross toolchain that emits APE
  binaries, normally fetched from [cosmo.zip](https://cosmo.zip/pub/cosmocc/);
  getgo consumes it via the clang-tool-chain PyPI package
  (`clang-tool-chain-cosmocpp` entry point, cosmocc 4.0.2 bundled for all
  five host platforms).
- Prior art for extract-and-delegate:
  [llamafile](https://github.com/mozilla-ai/llamafile), which extracts
  `/zip/` assets to `~/.llamafile/v/<version>/`.

See [DESIGN.md](DESIGN.md) for the full loader contract, builder pipeline,
platform caveats, decisions, and test strategy. Design lineage:
[soldr#2460](https://github.com/zackees/soldr/issues/2460).

## Status

Design phase. Implementation tracked in
[issue #1](https://github.com/zackees/getgo/issues/1). The
[`getgo`](https://pypi.org/project/getgo/) name on PyPI is reserved with a
0.0.1 placeholder.
