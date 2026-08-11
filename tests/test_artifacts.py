from __future__ import annotations

import json
import os
import subprocess
import venv
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_ape_is_fat_pe_polyglot() -> None:
    artifact = ROOT / "dist" / "getgo"
    assert artifact.is_file()
    data = artifact.read_bytes()
    assert data.startswith(b"MZqFpD")
    elf_machines = {
        int.from_bytes(data[offset + 18 : offset + 20], "little")
        for offset in range(len(data) - 20)
        if data[offset : offset + 4] == b"\x7fELF" and data[offset + 5] == 1
    }
    pe_machines = {
        int.from_bytes(data[offset + 4 : offset + 6], "little")
        for offset in range(len(data) - 6)
        if data[offset : offset + 4] == b"PE\0\0"
    }
    assert 183 in elf_machines
    assert 0x8664 in pe_machines
    assert b'"architectures":["x86_64","aarch64"]' in data
    assert b'"toolchain":"clang-tool-chain 1.5.8"' in data
    with zipfile.ZipFile(artifact) as archive:
        metadata = json.loads(archive.read("getgo-build.json"))
    assert metadata == {
        "architectures": ["x86_64", "aarch64"],
        "format": "Cosmopolitan APE/PE",
        "toolchain": "clang-tool-chain 1.5.8",
    }


@pytest.mark.skipif(os.environ.get("GETGO_TEST_BUILT_DISTS") != "1", reason="distribution smoke is opt-in")
def test_wheel_installs_outside_source_tree(tmp_path: Path) -> None:
    wheels = list((ROOT / "dist").glob("getgo-*.whl"))
    assert len(wheels) == 1
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    getgo = scripts / ("getgo.exe" if os.name == "nt" else "getgo")
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])], check=True)
    result = subprocess.run(
        [str(getgo), "--version"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "getgo 0.1.0\n"


def test_sdist_contains_release_sources() -> None:
    if os.environ.get("GETGO_TEST_BUILT_DISTS") != "1":
        pytest.skip("distribution smoke is opt-in")
    sdists = list((ROOT / "dist").glob("getgo-*.tar.gz"))
    assert len(sdists) == 1
