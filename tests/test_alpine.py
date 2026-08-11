from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_stock_alpine_bootstrap_chain_failure_and_reuse() -> None:
    if os.environ.get("GETGO_RUN_ALPINE") != "1":
        pytest.skip("set GETGO_RUN_ALPINE=1 to run the networked Docker gate")
    artifact = ROOT / "dist" / "getgo"
    assert artifact.is_file(), "build dist/getgo before running the Alpine gate"

    script = r"""
set -eu
export HOME=/tmp/getgo-home
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
cp /artifact/getgo /tmp/getgo
chmod +x /tmp/getgo

/bin/sh /tmp/getgo ruff pycowsay
ruff --version
pycowsay getgo
test -x "$HOME/.local/bin/uv"

real_uv="$HOME/.local/bin/uv"
mkdir -p /tmp/shim
cat > /tmp/shim/uv <<EOF
#!/bin/sh
echo "\$@" >> /tmp/uv-calls
exec "$real_uv" "\$@"
EOF
chmod +x /tmp/shim/uv
set +e
PATH="/tmp/shim:$PATH" /bin/sh /tmp/getgo getgo-package-that-does-not-exist-1 must-not-run
failure_code=$?
set -e
test "$failure_code" -ne 0
test "$(wc -l < /tmp/uv-calls)" -eq 1
grep -Fx "tool install getgo-package-that-does-not-exist-1" /tmp/uv-calls

cat > /tmp/shim/wget <<'EOF'
#!/bin/sh
echo invoked > /tmp/wget-was-invoked
exit 91
EOF
chmod +x /tmp/shim/wget
PATH="$HOME/.local/bin:/tmp/shim:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  /bin/sh /tmp/getgo ruff pycowsay
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
