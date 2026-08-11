# RED -> GREEN evidence

Issue #1 locked the public behavior before implementation. Commit `1a4abfb`
contains the unchanged conformance and Alpine assertions without either entry
point.

## RED

Run on 2026-08-11:

```text
GETGO_ENTRYPOINTS=python,ape pytest -q tests/test_cli_contract.py
16 failed, 6 skipped, 16 errors

GETGO_RUN_ALPINE=1 pytest -q tests/test_alpine.py
1 failed
```

The Python failures report `No module named getgo`; the APE and Alpine
failures report that `dist/getgo` does not exist. The same output is recorded
in the body of commit `1a4abfb`.

## GREEN

The implementation keeps every RED assertion. Later test changes are limited
to cross-platform APE fixture wiring and stronger checks for signal exits,
directory decoys, and Alpine's initially empty Python/uv state. The final local
and GitHub Actions commands and results are recorded in the issue-closing pull
request.

## Issue #3 PATH bootstrap RED

Issue #3 adds an explicit consent boundary and cross-shell PATH contract. The
2026-08-11 RED run of `uv run pytest tests/test_cli_contract.py -q` produced
20 failures: the CLI rejected both policy flags, every successful install ran
`uv tool update-shell`, XDG uv discovery and `GITHUB_PATH` were absent, the
nested uv installer could modify PATH, and Windows emitted only a PowerShell
activation command.

The GREEN suite runs the same black-box contract against the Python and APE
entry points. It includes non-TTY behavior, PTY acceptance/refusal, installer
environment isolation, idempotent startup files, XDG discovery, GitHub Actions
handoff, Unix shell-profile targets, and PowerShell/cmd.exe/Git Bash activation.
CI additionally boots fresh real Bash, zsh, fish, dash, Alpine ash, ksh, and
tcsh processes and requires each to resolve a Ruff installed by getgo.
