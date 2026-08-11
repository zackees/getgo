# getgo

**Install any PyPI tools on any machine with one downloaded file.**

## Install

Install `getgo` once for the current user—no administrator privileges needed.
Each command puts it in the standard user-local executable directory and makes
that directory available both now and in future login shells.

### Linux and macOS

```bash
bin="$HOME/.local/bin"; case "${SHELL##*/}:$(uname -s)" in zsh:*) profile="$HOME/.zprofile" ;; bash:Darwin) profile="$HOME/.bash_profile" ;; *) profile="$HOME/.profile" ;; esac; mkdir -p "$bin" && curl -LsSf https://github.com/zackees/getgo/releases/latest/download/getgo -o "$bin/getgo" && chmod +x "$bin/getgo" && { grep -Fqx 'export PATH="$HOME/.local/bin:$PATH"' "$profile" 2>/dev/null || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$profile"; } && export PATH="$bin:$PATH" && getgo --version
```

### Windows PowerShell

```powershell
$bin = Join-Path $HOME '.local\bin'; New-Item -ItemType Directory -Force -Path $bin | Out-Null; Invoke-WebRequest https://github.com/zackees/getgo/releases/latest/download/getgo -OutFile (Join-Path $bin 'getgo.com'); $userPath = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ }); if ($userPath -notcontains $bin) { [Environment]::SetEnvironmentVariable('Path', (($userPath + $bin) -join ';'), 'User') }; $env:Path = "$bin;$env:Path"; getgo --version
```

Windows saves the same download as `getgo.com` because it is a real PE
executable. Both installers use `~/.local/bin`, the same user-local tool
directory used by uv.

That's the whole installation. The same downloaded file runs natively on
Windows, macOS (Intel + Apple Silicon), and Linux (glibc + musl, x86_64 +
aarch64), unchanged. For example, install
[Ruff](https://pypi.org/project/ruff/), the popular Python linter and formatter,
from any terminal with:

```console
getgo ruff
ruff --version
```

Already have Python tooling? getgo is also a normal PyPI package with the
exact same CLI:

```bash
pip install getgo        # or: uv tool install --managed-python getgo@latest
getgo ruff
```

getgo is a **universal package installer**: same one-line API whether it
arrived as a curl'd file on a bare machine or via pip on a dev box. The
`.com` download exists for machines with *nothing* on them; `pip install
getgo` covers everywhere Python already is.

## Tool lifecycle

`getgo <package>` always means “make the latest release available as a
persistent per-user tool.” The same command handles first installation,
upgrades, and accidental repeats:

| Scenario | Command | Result |
|---|---|---|
| First install | `getgo ruff` | Installs the latest Ruff in an isolated uv environment. |
| Upgrade | `getgo ruff` | Refreshes the request and replaces an older Ruff with the latest release. |
| Already current | `getgo ruff` | Succeeds safely and leaves the current latest release installed. |
| Uninstall | `uv tool uninstall ruff` | Removes Ruff's launcher and environment; shared uv and managed Python remain. |

getgo intentionally stays install-only; after its first run, the bootstrapped
`uv` owns inspection (`uv tool list`) and removal (`uv tool uninstall`).

## The public API

```
getgo <package> [<package>...]
```

That line is the entire contract, and everything else in this project is
designed backward from it:

- **Every argument is a PyPI package name.** No subcommands, no verbs, no
  flag ceremony. `getgo ruff` installs `ruff`. (Only
  `--`-prefixed args are reserved: `--help`, `--version`.)
- **Every install follows one path:**
  `uv tool install --managed-python <package>@latest`.
  Each package gets a persistent, isolated per-user environment backed only
  by uv-managed Python; target tools are never installed into or based on
  system Python.
- **Each package's executables are immediately chainable when uv's tool bin
  directory is already on `PATH`.** The quick starts above predeclare the
  default `~/.local/bin` location, so `getgo ruff && ruff --version`
  resolves in the same shell line. getgo also asks uv to wire future shells.
  If a custom or inherited environment does not contain the directory,
  getgo prints the exact `export` or `$env:Path` command to run in the current
  shell; a child process cannot modify its parent shell.
- **Exit code**: 0 iff every package installed. First failure is reported
  with uv's error attached.
- **Distribution-independent**: the contract is identical from the curl'd
  `.com` file and from `pip install getgo` — two front doors, one API.
- Everything below this line — uv, APE, Cosmopolitan, ZIP config — is
  implementation detail and may change; this contract may not.

## Why

Shipping a CLI tool to "any machine" means N installers, or telling users to
install a package manager first. PyPI + uv already solve artifact hosting,
per-platform wheels, Python provisioning, and upgrades. The only missing
piece is a zero-prerequisite first step that works everywhere — that first
step is the *only* thing getgo is. It contains no payloads and no package
list: one generic ~200 KB trampoline, package name supplied at the prompt.

## Architecture

Three stages, each owned by the layer best at it:

```
 stage 0                    stage 1                     stage 2
 ┌─────────────────┐        ┌──────────────────┐        ┌──────────────────────┐
 │ getgo           │        │ ensure uv        │        │ uv tool install      │
 │ (APE loader,    │ ─────► │ (official        │ ─────► │ --managed-python     │
 │  ~200 KB, fat   │        │  installer, or   │        │ package@latest       │
 │  x86_64+arm64)  │        │  already there)  │        │ (isolated + PATH)    │
 └─────────────────┘        └──────────────────┘        └──────────────────────┘
```

### Stage 0 — the APE loader (getgo's code)

getgo is an [Actually Portable Executable](https://justine.lol/apeloader/): a
polyglot file that is simultaneously a Windows PE, a `#!/bin/sh` script, and
a carrier of embedded ELF/Mach-O images for **both x86_64 and aarch64**
(woven by `apelink`, so one file covers Intel and ARM). Written in a few
hundred lines of C++, compiled with `cosmoc++` from the
[clang-tool-chain](https://pypi.org/project/clang-tool-chain/) PyPI package —
the whole build runs under uv with no host toolchain.

Startup per platform:

| Platform | How the file starts |
|---|---|
| Windows x64 | It *is* a native PE — direct execution. |
| Linux glibc/musl, BSDs | `/bin/sh` header execs `ape` from `$PATH`, else self-extracts the ~10 KB loader to `$TMPDIR/ape` and re-execs. Works under busybox `ash` (Alpine). |
| macOS Intel | Native path via the same shell bootstrap. |
| macOS ARM | Loader compiled from embedded source with local `cc` on first run (needs Xcode CLT) or found at `/usr/local/bin/ape`. |

### Stage 1 — uv bootstrap, per target

The loader never links a TLS stack. If `uv` isn't on `PATH` (or at its
default install dir), it invokes the official installers via tools every OS
ships, and those installers do their own platform/arch/libc detection against
uv's prebuilt artifacts:

| Target | Trigger | uv artifact installed |
|---|---|---|
| linux **x64 glibc** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` (fallback `wget -qO-`) | `uv-x86_64-unknown-linux-gnu` |
| linux **arm64 glibc** | same | `uv-aarch64-unknown-linux-gnu` |
| linux **x64 musl** (Alpine) | same — busybox `wget` suffices | `uv-x86_64-unknown-linux-musl` (static) |
| linux **arm64 musl** | same | `uv-aarch64-unknown-linux-musl` (static) |
| macOS x64 / arm64 | same | `uv-{x86_64,aarch64}-apple-darwin` |
| Windows x64 | `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` | `uv-x86_64-pc-windows-msvc` |

uv's musl builds are fully static, so the same binary serves Alpine, Void,
and any other musl distro — no glibc compatibility question on the bootstrap
path.

### Stage 2 — delegation to uv

The loader spawns `uv tool install --managed-python <package>@latest`, waits,
and forwards the exit code (spawn/wait is the one shape that behaves
identically everywhere — Cosmopolitan's `execve` on Windows spawns rather
than replaces). The flag forbids uv from selecting a system interpreter, and
`@latest` makes first install, upgrade, and repeat use one request. uv then:

- resolves the right wheel for the host (`win_amd64`, `macosx_*`,
  `manylinux_*_{x86_64,aarch64}`, `musllinux_*_{x86_64,aarch64}`),
- selects a compatible **uv-managed CPython**, downloading one when needed —
  including musl builds, so a stock Alpine container with no Python works,
- installs into a persistent isolated environment and links the executable
  into uv's XDG/user executable directory (`~/.local/bin` by default).

getgo always finishes by ensuring PATH wiring for future shells (uv's
`uv tool update-shell`) and, if the *current* shell can't resolve
that executable directory, printing the exact one-line `export`/`$env:` fix —
in service of the public-API guarantee that installed tools chain immediately.

**Requirement this places on a package:** it must publish wheels for the
platforms its users run — *including `musllinux` wheels* if Alpine matters.
getgo can't conjure a wheel that doesn't exist.

## White-label installers (optional, later)

`uvx getgo bake --package ruff -o ruff-setup.com` produces a renamed copy
with the package name pre-baked as a `/zip/getgo.json` config entry (APE
files are valid ZIP archives; Cosmopolitan exposes entries under `/zip/…`).
Same loader, zero-argument UX for your users. Secondary surface — the hosted
generic binary is the product.

## Repository layout

- `loader/` — the C++ APE loader (`cosmoc++`, `-mtiny`, fat x86_64+aarch64)
- `src/getgo/` — the Python implementation distributed through PyPI
- `scripts/` — reproducible APE build and architecture validation
- `tests/` — the shared black-box contract plus the Alpine-musl merge gate

## Development

[uv](https://docs.astral.sh/uv/) is the only development prerequisite.

```bash
uv sync --locked --extra test --extra ape
uv run ruff check .
uv run pytest -q tests/test_cli_contract.py
uv run python scripts/build_ape.py
GETGO_ENTRYPOINTS=python,ape uv run pytest -q tests/test_cli_contract.py
GETGO_RUN_ALPINE=1 uv run pytest -q tests/test_alpine.py
uv build
```

The APE build pins `clang-tool-chain==1.5.8` in `pyproject.toml` and
`uv.lock`. It writes toolchain and architecture metadata into the APE's ZIP
directory and validates both the AMD64 PE and embedded AArch64 ELF before
release. See [`docs/RED_GREEN.md`](docs/RED_GREEN.md) for the preserved
test-first evidence.

Package arguments must be valid PyPI distribution names: ASCII letters,
digits, `.`, `_`, and `-`, beginning and ending with a letter or digit. They
are always passed to uv as individual process arguments and are never shell
evaluated.

## Background: Cosmopolitan, APE, and the toolchain

Primer for implementers — everything getgo builds on:

- **[Cosmopolitan Libc](https://github.com/jart/cosmopolitan)** makes C/C++
  "build-once run-anywhere": one artifact runs natively on Linux, Windows,
  macOS, and the BSDs, on x86_64 and aarch64.
- **APE** is its polyglot output format (`MZ` header for Windows, `#!/bin/sh`
  for Unix, embedded ELF/Mach-O images). `--assimilate` converts a copy
  in-place to a plain native binary.
- **Every APE is also a valid ZIP archive.** Entries appended to the
  executable appear at runtime under `/zip/…` with transparent
  decompression. getgo uses this only for the optional baked config — never
  payloads (Cosmopolitan cannot `exec()` from `/zip/`, and getgo doesn't
  need to).
- **cosmocc / cosmoc++** is the GCC+Clang cross toolchain that emits APE
  binaries, normally fetched from [cosmo.zip](https://cosmo.zip/pub/cosmocc/);
  getgo consumes it via the clang-tool-chain PyPI package
  (`clang-tool-chain-cosmocpp` entry point, cosmocc 4.0.2 bundled for all
  five host platforms).
- Prior art for the bootstrap-and-delegate pattern:
  [llamafile](https://github.com/mozilla-ai/llamafile).

See [DESIGN.md](DESIGN.md) for the full loader contract, platform caveats,
decisions, and test strategy. Design lineage:
[soldr#2460](https://github.com/zackees/soldr/issues/2460).

## License

BSD-3-Clause. See [LICENSE](LICENSE).
