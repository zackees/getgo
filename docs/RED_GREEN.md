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
