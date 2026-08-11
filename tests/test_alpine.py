from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_stock_alpine_without_python_bootstraps_managed_python_and_installs_ruff() -> None:
    if os.environ.get("GETGO_RUN_ALPINE") != "1":
        pytest.skip("set GETGO_RUN_ALPINE=1 to run the networked Docker gate")
    artifact = ROOT / "dist" / "getgo"
    assert artifact.is_file(), "build dist/getgo before running the Alpine gate"

    script = r"""
set -eu
export HOME=/tmp/getgo-home
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
! command -v python
! command -v python3
! command -v uv
cp /artifact/getgo /tmp/getgo
chmod +x /tmp/getgo

# A real package install must bootstrap uv and a user-managed Python.
/bin/sh /tmp/getgo ruff
ruff --version | grep -F "ruff "
test -x "$HOME/.local/bin/uv"
test ! -e /usr/bin/python
test ! -e /usr/bin/python3

tool_bin="$("$HOME/.local/bin/uv" tool dir --bin)"
test "$tool_bin" = "$HOME/.local/bin"
test -x "$tool_bin/ruff"
python_dir="$("$HOME/.local/bin/uv" python dir)"
managed_python="$("$HOME/.local/bin/uv" python find --managed-python --no-python-downloads)"
test -x "$managed_python"
case "$managed_python" in
  "$python_dir"/*) ;;
  *) echo "managed Python escaped uv's directory: $managed_python" >&2; exit 1 ;;
esac
tool_python="$("$HOME/.local/bin/uv" tool dir)/ruff/bin/python"
test -x "$tool_python"
tool_base_prefix="$("$tool_python" -c "import sys; print(sys.base_prefix)")"
case "$tool_base_prefix" in
  "$python_dir"/*) ;;
  *) echo "Ruff does not use uv's managed Python: $tool_base_prefix" >&2; exit 1 ;;
esac
"$HOME/.local/bin/uv" tool list | grep -F "ruff "

# A failed package must stop the chain and preserve uv's exit status.
real_uv="$HOME/.local/bin/uv"
mkdir -p /tmp/shim
cat > /tmp/shim/uv <<EOF
#!/bin/sh
echo "\$@" >> /tmp/uv-calls
exit 37
EOF
chmod +x /tmp/shim/uv
set +e
PATH="/tmp/shim:$PATH" /bin/sh /tmp/getgo getgo-package-that-does-not-exist-1 must-not-run
failure_code=$?
set -e
test "$failure_code" -eq 37
test "$(wc -l < /tmp/uv-calls)" -eq 1
grep -Fx "tool install getgo-package-that-does-not-exist-1" /tmp/uv-calls

# A subsequent install must reuse uv instead of running the bootstrap again.
cat > /tmp/shim/wget <<'EOF'
#!/bin/sh
echo invoked > /tmp/wget-was-invoked
exit 91
EOF
chmod +x /tmp/shim/wget
PATH="$HOME/.local/bin:/tmp/shim:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  /bin/sh /tmp/getgo ruff
test ! -e /tmp/wget-was-invoked
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{artifact.parent.resolve()}:/artifact:ro",
            "alpine:3.22",
            "/bin/sh",
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
