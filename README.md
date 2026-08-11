# getgo

**Install any PyPI tools on any machine with one downloaded file.**

## Install

Install `getgo` once for the current user—no administrator privileges needed.
Each command puts it in `~/.local/bin`, makes it available in the current
shell, and persists that user-local directory for future shells.

### Linux and macOS

```bash
bin="$HOME/.local/bin"; line='case ":$PATH:" in *:"$HOME/.local/bin":*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac'; mkdir -p "$bin" && curl -LsSf https://github.com/zackees/getgo/releases/latest/download/getgo -o "$bin/getgo" && chmod +x "$bin/getgo" && for profile in "$HOME/.profile" "$HOME/.bashrc" "$HOME/.zshenv"; do grep -Fqx "$line" "$profile" 2>/dev/null || printf '\n%s\n' "$line" >> "$profile"; done && case ":$PATH:" in *:"$bin":*) ;; *) export PATH="$bin:$PATH" ;; esac && getgo --version
```

For fish, use its native universal-path command after the download:

```fish
set bin "$HOME/.local/bin"; mkdir -p "$bin"; curl -LsSf https://github.com/zackees/getgo/releases/latest/download/getgo -o "$bin/getgo"; chmod +x "$bin/getgo"; fish_add_path --global "$bin"; getgo --version
```

### Windows

PowerShell:

```powershell
$bin = Join-Path $HOME '.local\bin'; New-Item -ItemType Directory -Force -Path $bin | Out-Null; Invoke-WebRequest https://github.com/zackees/getgo/releases/latest/download/getgo -OutFile (Join-Path $bin 'getgo.com'); $userPath = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ }); if ($userPath -notcontains $bin) { [Environment]::SetEnvironmentVariable('Path', (($bin + $userPath) -join ';'), 'User') }; if (($env:Path -split ';') -notcontains $bin) { $env:Path = "$bin;$env:Path" }; getgo --version
```

Command Prompt (`cmd.exe`):

```bat
powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin=Join-Path $HOME '.local\bin'; New-Item -ItemType Directory -Force -Path $bin | Out-Null; Invoke-WebRequest https://github.com/zackees/getgo/releases/latest/download/getgo -OutFile (Join-Path $bin 'getgo.com'); $p=@([Environment]::GetEnvironmentVariable('Path','User') -split ';' | Where-Object { $_ }); if ($p -notcontains $bin) { [Environment]::SetEnvironmentVariable('Path',(($bin+$p)-join ';'),'User') }" && set "PATH=%USERPROFILE%\.local\bin;%PATH%" && getgo --version
```

Git Bash:

```bash
bin="$HOME/.local/bin"; mkdir -p "$bin" && curl -LsSf https://github.com/zackees/getgo/releases/latest/download/getgo -o "$bin/getgo.com" && powershell.exe -NoProfile -ExecutionPolicy Bypass -Command '$bin=Join-Path $HOME ".local\bin"; $p=@([Environment]::GetEnvironmentVariable("Path","User") -split ";" | Where-Object { $_ }); if ($p -notcontains $bin) { [Environment]::SetEnvironmentVariable("Path",(($bin+$p)-join ";"),"User") }' && case ":$PATH:" in *:"$bin":*) ;; *) export PATH="$bin:$PATH" ;; esac && getgo --version
```

Windows saves the download as `getgo.com` because it is a native PE
executable. All three commands persist the same per-user directory in the
Windows user PATH, so a new PowerShell, Command Prompt, or Git Bash process
can resolve `getgo`.

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
getgo [--yes | --no-modify-path] <package> [<package>...]
```

That line is the entire contract, and everything else in this project is
designed backward from it:

- **Every positional argument is a PyPI package name.** No subcommands or
  verbs: `getgo ruff` installs `ruff`. The only behavior flags are `--yes`
  and `--no-modify-path`, alongside `--help` and `--version`.
- **Every install follows one path:**
  `uv tool install --managed-python <package>@latest`.
  Each package gets a persistent, isolated per-user environment backed only
  by uv-managed Python; target tools are never installed into or based on
  system Python.
- **PATH setup is explicit and verified.** If uv or an installed tool lands
  outside the current `PATH`, an interactive getgo asks before changing a
  startup file or the Windows user PATH; Enter means yes. `--yes` is the
  automation-friendly opt-in, while `--no-modify-path` guarantees no change.
  A noninteractive invocation never prompts or edits without `--yes`.
  GitHub Actions is detected through `GITHUB_PATH` and configured for the
  next step. getgo always prints current-shell activation commands because a
  child process cannot change its parent shell.
- **Exit code**: 0 iff every package installed. First failure is reported
  with uv's error attached.
- **Distribution-independent**: the contract is identical from the curl'd
  `.com` file and from `pip install getgo` — two front doors, one API.
- Everything below this line — uv, APE, Cosmopolitan, ZIP config — is
  implementation detail and may change; this contract may not.

## PATH bootstrap

`~/.local/bin` is the XDG-recommended user executable directory and is uv's
default on Linux and macOS, but it is not universally present on `PATH`.
Debian and Ubuntu commonly add it conditionally; a stock Alpine shell does
not. getgo therefore tests the actual process PATH instead of assuming a
distribution configured it.

getgo discovers uv through `PATH`, `UV_INSTALL_DIR`, `UV_UNMANAGED_INSTALL`,
`XDG_BIN_HOME`, the `bin` sibling of `XDG_DATA_HOME`, and finally
`~/.local/bin`. It asks uv for the independently configurable tool launcher
directory with `uv tool dir --bin`; both directories are handled when they
differ. During uv bootstrap, `UV_NO_MODIFY_PATH=1` prevents the nested
installer from changing anything before getgo has permission.

After consent, getgo consults `uv tool update-shell`. On Unix it deliberately
writes its own guarded configuration, because uv's generated export can add a
duplicate entry when a startup file is sourced again. That idempotent path
covers POSIX sh, dash, Alpine ash, Bash, zsh, fish, ksh, tcsh, and Nushell. On
Windows, uv's per-user registry update makes the same directory visible to new
PowerShell, Command Prompt, and Git Bash processes. Existing startup-file
content is preserved. If setup is refused, impossible, or unnecessary for the
current process, getgo prints exact activation commands and still preserves
the successful package-install exit code.

For scripts, choose the policy explicitly:

```console
getgo --yes ruff             # persist a missing PATH automatically
getgo --no-modify-path ruff  # install only; print activation instructions
```

The equivalent environment switches are `GETGO_YES=1` and
`GETGO_NO_MODIFY_PATH=1`.

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

getgo always finishes by checking both uv's directory and `uv tool dir --bin`.
With consent, it consults uv's shell support, writes guarded Unix startup
configuration itself, and uses uv's Windows user-registry support. It also
prints current-process commands for POSIX shells and for PowerShell, Command
Prompt, and Git Bash, because a child process cannot rewrite its parent's
environment.

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
