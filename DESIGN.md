# getgo — design

Ported and generalized from [soldr#2460](https://github.com/zackees/soldr/issues/2460)
(2026-08-11). soldr is the first customer, not a dependency.

## Public API — the contract everything is designed around

```
export PATH="$HOME/.local/bin:$PATH" && curl <url> -o getgo && ./getgo [--yes | --no-modify-path] <package>... && <tool> --version
```

Owner directive (2026-08-11): this line **is** the product. Constraints it
imposes, in priority order:

1. **Every positional arg is a PyPI package name.** No verbs or subcommands.
   `getgo ruff` installs `ruff`. `--yes` opts into unattended PATH setup and
   `--no-modify-path` forbids it; `--help` and `--version` are informational.
2. **Immediately-chainable result with the tool directory predeclared**:
   the documented quick start places `~/.local/bin` on `PATH`, so each
   package's executables resolve in the same shell line (`getgo ruff &&
   ruff --version`). PATH lookup is per-command, so binaries appearing there
   mid-chain resolve without a new shell. When a directory is missing, getgo
   obtains consent, consults `uv tool update-shell`, writes guarded Unix shell
   profiles itself, and prints current-process commands; a child cannot modify
   its parent shell.
3. **One hosted generic file** at a stable URL (GitHub Releases
   `latest/download/getgo`). No per-tool artifact required for the primary
   flow.
4. **Exit code**: 0 iff every named package installed; first failure
   reported with uv's stderr attached.

## Product

1. **`loader/`** — a small C++ program (~150–400 lines) compiled with
   `cosmoc++` into a fat APE (x86_64 + aarch64 via `apelink`; `-mtiny` keeps
   the empty loader ~180 KB). Behavior: parse argv (package list) → ensure
   uv → `uv tool install --managed-python <pkg>@latest` per package → PATH
   wiring → forward exit code. This binary, published to GitHub Releases, is
   the deliverable.
2. **`getgo` (PyPI)** — the same public API as a normal Python package:
   `pip install getgo` puts a `getgo` CLI on PATH implementing the identical
   contract (`getgo <package>...` → ensure uv → managed `uv tool install` each).
   The `.com` file serves machines with nothing installed; the PyPI package
   serves machines that already have Python — two front doors, one API.
   Also hosts the secondary builder surface (`getgo bake --package <name>
   [-o out.com]` → white-label copy with the package list pre-baked as
   `/zip/getgo.json`) and CI packaging.

## Loader runtime contract

Grounded in the APE/Cosmopolitan research (sources at bottom):

- **Startup**: the APE polyglot runs natively as PE on Windows. On Linux/BSD
  the `#!/bin/sh` header execs an `ape` loader from `$PATH`, else self-extracts
  the ~10 KB embedded loader to `$TMPDIR/ape` (fallback `/tmp/ape`) and
  re-execs. On macOS ARM the loader is compiled from embedded source with the
  local `cc` (first run needs Xcode CLT) or found at `/usr/local/bin/ape`.
- **Package selection**: argv is primary (public API). The optional baked
  config is read only when present: Cosmopolitan libc resolves its own
  executable path (`GetProgramExecutableName`), parses its own ZIP central
  directory, and exposes entries under `/zip/…` with transparent
  deflate-on-read — getgo stores at most one entry, `/zip/getgo.json`
  (default package list, optional uv pin). argv packages append to baked
  ones.
- **No exec from `/zip/`**: direct exec of ZIP members is compiled out in
  Cosmopolitan (`libc/proc/execve.c`, the zipos branch is `if (0 && …)`).
  Irrelevant to getgo's config-only use, but it is the reason the fat-binary
  design was dropped: payload extraction machinery (content-addressed cache,
  atomic rename, GC) all became unnecessary once uv owns artifact delivery.
- **uv detection**: check `PATH`, `UV_INSTALL_DIR`, `UV_UNMANAGED_INSTALL`,
  `XDG_BIN_HOME`, the `bin` sibling of `XDG_DATA_HOME`, then uv's default
  locations (`~/.local/bin/uv`, `%USERPROFILE%\.local\bin\uv.exe`). Present
  → skip install (idempotent re-runs).
- **uv installation**: spawn the official installers via preinstalled tools —
  Unix: `curl -LsSf https://astral.sh/uv/install.sh | sh`, fallback
  `wget -qO- … | sh` (busybox wget suffices on Alpine); Windows:
  `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
  The child receives `UV_NO_MODIFY_PATH=1`, preserving getgo's consent
  boundary. No TLS/HTTP client is linked into the loader.
- **Delegation**: `uv tool install --managed-python <package>@latest`. Chosen
  over `uv pip install --system`, which requires a pre-existing system Python
  and fights PEP 668 externally-managed distros. `--managed-python` forbids
  the system-interpreter fallback, while `@latest` gives first install,
  upgrade, and accidental repeat one idempotent request. uv downloads a
  compatible managed Python when needed, installs into a persistent isolated
  environment, and links the executable into its XDG/user executable
  directory. Uninstall remains uv's native `uv tool uninstall <package>`.
- **Spawn semantics**: spawn + wait + forward exit code everywhere
  (Cosmopolitan's `execve` on Windows spawns a child rather than replacing
  the process, so spawn/wait is the one portable shape).
- **Post-install**: query the executable directory with `uv tool dir --bin`
  and independently check it and uv's directory. Missing directories trigger
  an interactive default-yes prompt; `--yes`/`GETGO_YES=1` opts in without a
  TTY, while `--no-modify-path`/`GETGO_NO_MODIFY_PATH=1` forbids mutation.
  Noninteractive runs never prompt or edit implicitly. GitHub Actions uses
  `GITHUB_PATH`. Otherwise getgo consults `uv tool update-shell`. On Unix it
  exposes the tool directory to that probe so uv makes no unguarded edits,
  then getgo writes idempotent configuration for sh/dash/ash, Bash, zsh,
  fish, ksh, tcsh, and Nushell. On Windows uv persists the per-user registry
  PATH shared by PowerShell, cmd.exe, and Git Bash, with a getgo registry
  fallback for a distinct uv directory or uv failure. Exact current-process
  commands are always printed when needed. A PATH failure cannot turn
  successful package installation into failure.

## Builder pipeline (`getgo bake`)

1. Obtain the loader binary. First cut: compile at bake time via
   `clang-tool-chain-cosmocpp` (clang-tool-chain PyPI package bundles cosmocc
   4.0.2 for win-x64 / linux-x64 / linux-arm64 / mac-x64 / mac-arm64).
   Later optimization: ship a prebuilt loader in the getgo wheel and skip
   compilation entirely — then `bake` is sub-second and toolchain-free.
2. Append `getgo.json` as a ZIP entry. APE files are valid ZIPs (central
   directory at EOF); Python stdlib `zipfile` in mode `"a"` explicitly
   supports appending an archive to a non-ZIP-prefixed file (the
   self-extracting-exe pattern). Fallback if zipos rejects the layout: the
   modified Info-ZIP `zip` shipped with cosmocc.
3. Rename to `<out>.com`, set executable bit.

## Testing — musl gate, RED first

The merge bar is an Alpine (musl x86_64) Docker container, chosen because it
is the least forgiving substrate: busybox sh, no glibc, no preinstalled uv or
Python.

- **Lifecycle gate**: pytest runs the generic `getgo` APE via `/bin/sh` and
  installs Ruff in the container. It asserts that uv and a managed Python are
  bootstrapped without consulting a discoverable system-Python decoy, Ruff's
  launcher and environment use uv's user directories, an old pinned Ruff is
  upgraded back to the exact initially observed latest release, an accidental
  duplicate succeeds without re-bootstrapping uv, the first failed package
  stops the chain with its exact exit code, and `uv tool uninstall ruff`
  removes only Ruff while retaining uv and the managed interpreter.
- Unit tests (no container, no network): config ZIP append + re-read offsets,
  uv-presence detection, per-OS installer-command selection, argv mapping.
- Windows / macOS smoke rides CI runners later; musl is the standing gate.

## Platform caveats (accepted, documented)

- **macOS ARM first run needs Xcode CLT** (to compile the ape loader) unless
  `ape` is preinstalled; `--assimilate` converts a copy to native Mach-O as
  an escape hatch. Acceptable for a developer-tool audience.
- **Wine binfmt_misc**: desktop Linux boxes with Wine registered for `MZ` can
  hijack APE binaries; the fix is a more specific binfmt rule or the `ape`
  loader — a docs FAQ entry, not getgo code.
- **Network is required on first run** (uv installer + wheel download). The
  fat-binary offline story was explicitly traded away.
- **Target package must ship wheels for the user's platform** — including
  musllinux for Alpine users. getgo's docs must call this out to tool
  authors (soldr: verify musllinux wheels are published before switchover).

## Decisions

- **Loader in C++ via cosmoc++, not Rust**: no official Rust cosmo target;
  the community path (rust-ape-example) needs nightly + custom target JSON
  and is x86-64-only in practice. The loader is small; everything real
  lives in the target tool.
- **Config-only ZIP use** — payloads stay on PyPI. This is the core design
  bet: uv is the universal installer; getgo is only the universal *first
  step*.
- **`uv tool install --managed-python <package>@latest`** over
  `uv pip install --system` (one persistent per-user tool path, isolated
  environments, repeatable upgrades, no system-Python dependency, and no
  PEP 668 conflict).
- **Official uv install scripts over vendored downloads**: the scripts do
  their own platform/arch/libc detection; pinning is offered via config
  (`UV_INSTALLER_VERSION`-style env or versioned script URL) not vendoring.

## Release implementation

- The build dependency is pinned to `clang-tool-chain==1.5.8`; the uv
  bootstrap intentionally follows uv's latest official installer.
- `uv run python scripts/build_ape.py` produces `dist/getgo` and removes its
  temporary architecture/debug sidecars before returning.
- GitHub Actions runs deterministic Python conformance on Windows, macOS, and
  Linux, shared Python+APE conformance on Linux, native APE install/PATH/exit
  smoke tests on Windows and macOS, artifact smoke tests, and the stock-Alpine
  network gate.
- A matching `v*` tag repeats the artifact tests, publishes the wheel and
  sdist through PyPI trusted publishing, and attaches the identical tested APE
  bytes as the GitHub Release asset named `getgo`.
- The project is released under BSD-3-Clause.

## Sources

[cosmocc README](https://github.com/jart/cosmopolitan/blob/master/tool/cosmocc/README.md) ·
[execve.c (zipos exec disabled)](https://github.com/jart/cosmopolitan/blob/master/libc/proc/execve.c) ·
[APE loader](https://justine.lol/apeloader/) ·
[llamafile cache pattern](https://deepwiki.com/mozilla-ai/llamafile/4.3-gpu-backends) ·
[rust-ape-example](https://github.com/ahgamut/rust-ape-example) ·
[clang-tool-chain](https://pypi.org/project/clang-tool-chain/) ·
[LWN: Truly portable C applications](https://lwn.net/Articles/997238/)
