from __future__ import annotations

import os
import subprocess
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_ape_is_fat_pe_polyglot() -> None:
    artifact = ROOT / "dist" / "getgo"
    assert artifact.is_file()
    data = artifact.read_bytes()
    assert data.startswith(b"MZqFpD")
    assert b"x86_64" in data
    assert b"aarch64" in data


@pytest.mark.skipif(os.environ.get("GETGO_TEST_BUILT_DISTS") != "1", reason="distribution smoke is opt-in")
def test_wheel_installs_outside_source_tree(tmp_path: Path) -> None:
    wheels = list((ROOT / "dist").glob("getgo-*.whl"))
    assert len(wheels) == 1
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])], check=True)
    result = subprocess.run(
        [str(python), "-m", "getgo", "--version"],
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
