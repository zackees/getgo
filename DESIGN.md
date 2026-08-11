# getgo — design

Ported and generalized from [soldr#2460](https://github.com/zackees/soldr/issues/2460)
(2026-08-11). soldr is the first customer, not a dependency.

## Public API — the contract everything is designed around

```
curl <url> -o getgo && ./getgo <package> [<package>...] && <tool> --version
```

Owner directive (2026-08-11): this line **is** the product. Constraints it
imposes, in priority order:

1. **Every positional arg is a PyPI package name.** No verbs, no
   subcommands. `getgo soldr reld` installs `soldr` and `reld`. Reserved
   namespace: `--`-prefixed args only (`--help`, `--version`).
2. **Immediately-chainable result**: after exit 0, each package's
   executables must resolve in the same shell line (`soldr --version &&
   red --version`). Mechanism: uv installs into `~/.local/bin`; PATH lookup
   is per-command so new binaries there resolve mid-chain on any system
   where that dir is already on PATH; getgo wires future shells silently
   (`uv tool update-shell`) and prints the exact one-line fix when the
   current shell can't resolve — never silently succeeds into a broken
   chain.
3. **One hosted generic file** at a stable URL (GitHub Releases
   `latest/download/getgo`). No per-tool artifact required for the primary
   flow.
4. **Exit code**: 0 iff every named package installed; first failure
   reported with uv's stderr attached.

## Product

1. **`loader/`** — a small C++ program (~150–400 lines) compiled with
   `cosmoc++` into a fat APE (x86_64 + aarch64 via `apelink`; `-mtiny` keeps
   the empty loader ~180 KB). Behavior: parse argv (package list) → ensure
   uv → `uv tool install <pkg>` per package → PATH wiring → forward exit
   code. This binary, published to GitHub Releases, is the deliverable.
2. **`getgo` (PyPI)** — the same public API as a normal Python package:
   `pip install getgo` puts a `getgo` CLI on PATH implementing the identical
   contract (`getgo <package>...` → ensure uv → `uv tool install` each).
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
- **uv detection**: check `PATH`, then uv's default install locations
  (`~/.local/bin/uv`, `%USERPROFILE%\.local\bin\uv.exe`). Present → skip
  install (idempotent re-runs).
- **uv installation**: spawn the official installers via preinstalled tools —
  Unix: `curl -LsSf https://astral.sh/uv/install.sh | sh`, fallback
  `wget -qO- … | sh` (busybox wget suffices on Alpine); Windows:
  `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
  No TLS/HTTP client is linked into the loader.
- **Delegation**: `uv tool install <package>`. Chosen over
  `uv pip install --system`, which requires a pre-existing system Python and
  fights PEP 668 externally-managed distros. `uv tool install` provisions a
  managed CPython when the host has none, installs into an isolated env, and
  links the executable into `~/.local/bin`.
- **Spawn semantics**: spawn + wait + forward exit code everywhere
  (Cosmopolitan's `execve` on Windows spawns a child rather than replacing
  the process, so spawn/wait is the one portable shape).
- **Post-install**: if the installed tool is not resolvable on `PATH`, print
  uv's PATH hint. Never mutate shell rc files.

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

- **RED**: pytest that runs the generic `getgo` binary via `/bin/sh` inside
  the container against **two** tiny known packages (the public-API shape:
  `./getgo <a> <b> && <a> --version && <b> --version` as one shell line)
  and asserts (a) uv gets installed, (b) both tools chain immediately,
  (c) exit code 0, and non-zero when a bogus package is included,
  (d) a second run skips the uv install (idempotence).
  **GREEN**: loader + builder implementation, no test edits beyond imports.
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
- **`uv tool install`** over `uv pip install --system` (PEP 668 / no-Python
  hosts).
- **Official uv install scripts over vendored downloads**: the scripts do
  their own platform/arch/libc detection; pinning is offered via config
  (`UV_INSTALLER_VERSION`-style env or versioned script URL) not vendoring.

## Open questions

- Pin uv by default at bake time (reproducibility) vs latest (freshness)?
  Leaning: `--uv-version` optional, default latest.
- Ship a prebuilt loader in the getgo wheel from v0.2 (removes
  clang-tool-chain from the bake path entirely)?
- `run <pkg> …` verb (`uvx`-style ephemeral execution) as a follow-up
  surface?
- License (owner call before first release).

## Sources

[cosmocc README](https://github.com/jart/cosmopolitan/blob/master/tool/cosmocc/README.md) ·
[execve.c (zipos exec disabled)](https://github.com/jart/cosmopolitan/blob/master/libc/proc/execve.c) ·
[APE loader](https://justine.lol/apeloader/) ·
[llamafile cache pattern](https://deepwiki.com/mozilla-ai/llamafile/4.3-gpu-backends) ·
[rust-ape-example](https://github.com/ahgamut/rust-ape-example) ·
[clang-tool-chain](https://pypi.org/project/clang-tool-chain/) ·
[LWN: Truly portable C applications](https://lwn.net/Articles/997238/)
