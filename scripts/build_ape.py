from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "loader" / "getgo.cpp"
OUTPUT = ROOT / "dist" / "getgo"
TOOLCHAIN_VERSION = "1.5.8"


def main() -> int:
    compiler = shutil.which("clang-tool-chain-cosmocpp")
    if compiler is None:
        raise SystemExit("clang-tool-chain-cosmocpp is unavailable; run with `uv run --extra ape`")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="getgo-build-") as temporary:
        temporary_output = Path(temporary) / "getgo.com"
        command = [
            compiler,
            "-std=c++17",
            "-Os",
            "-mtiny",
            "-s",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(SOURCE),
            "-o",
            str(temporary_output),
        ]
        environment = os.environ.copy()
        environment.setdefault("SOURCE_DATE_EPOCH", "0")
        subprocess.run(command, cwd=ROOT, env=environment, check=True)
        metadata = {
            "architectures": ["x86_64", "aarch64"],
            "format": "Cosmopolitan APE/PE",
            "toolchain": f"clang-tool-chain {TOOLCHAIN_VERSION}",
        }
        entry = zipfile.ZipInfo("getgo-build.json", date_time=(1980, 1, 1, 0, 0, 0))
        entry.compress_type = zipfile.ZIP_STORED
        entry.external_attr = 0o644 << 16
        with zipfile.ZipFile(temporary_output, "a", allowZip64=False) as archive:
            archive.writestr(entry, json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n")
        data = temporary_output.read_bytes()
        if not data.startswith(b"MZqFpD"):
            raise SystemExit("compiler output is not an APE/PE polyglot")
        staged = OUTPUT.with_suffix(".tmp")
        staged.write_bytes(data)
        staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(staged, OUTPUT)

    print(f"built {OUTPUT.relative_to(ROOT)} ({OUTPUT.stat().st_size} bytes) with clang-tool-chain {TOOLCHAIN_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
