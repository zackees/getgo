from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALL_URL_UNIX = "https://astral.sh/uv/install.sh"
INSTALL_URL_WINDOWS = "https://astral.sh/uv/install.ps1"


@dataclass(frozen=True)
class EntryPoint:
    name: str
    command: tuple[str, ...]


@dataclass
class FakeUv:
    executable: Path
    bin_dir: Path
    tool_bin: Path
    log: Path
    home: Path
    env: dict[str, str]

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


def _entrypoint_params() -> list[str]:
    value = os.environ.get("GETGO_ENTRYPOINTS", "python")
    return [part.strip() for part in value.split(",") if part.strip()]


@pytest.fixture(params=_entrypoint_params())
def entrypoint(request: pytest.FixtureRequest, tmp_path: Path) -> EntryPoint:
    if request.param == "python":
        return EntryPoint("python", (sys.executable, "-m", "getgo"))
    if request.param == "ape":
        artifact = ROOT / "dist" / "getgo"
        assert artifact.is_file(), "build dist/getgo before running APE conformance tests"
        if os.name == "nt":
            windows_artifact = tmp_path / "getgo.com"
            shutil.copyfile(artifact, windows_artifact)
            return EntryPoint("ape", (str(windows_artifact),))
        artifact.chmod(artifact.stat().st_mode | stat.S_IXUSR)
        return EntryPoint("ape", (str(artifact),))
    raise AssertionError(f"unknown entry point: {request.param}")


def run_getgo(
    entrypoint: EntryPoint,
    args: list[str],
    env: dict[str, str],
    *,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env)
    source_path = str(ROOT / "src")
    old_pythonpath = merged.get("PYTHONPATH")
    merged["PYTHONPATH"] = source_path if not old_pythonpath else os.pathsep.join((source_path, old_pythonpath))
    return subprocess.run(
        [*entrypoint.command, *args],
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_uv(tmp_path: Path) -> FakeUv:
    fake_root = tmp_path / "fake"
    bin_dir = fake_root / "bin"
    tool_bin = fake_root / "tools"
    home = tmp_path / "home"
    log = tmp_path / "uv-calls.jsonl"
    bin_dir.mkdir(parents=True)
    tool_bin.mkdir()
    home.mkdir()

    script = tmp_path / "fake_uv.py"
    script.write_text(
        """import json
import os
import sys

with open(os.environ["GETGO_FAKE_UV_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")

args = sys.argv[1:]
if args == ["tool", "dir", "--bin"]:
    print(os.environ["GETGO_FAKE_TOOL_BIN"])
    raise SystemExit(int(os.environ.get("GETGO_FAKE_DIR_EXIT", "0")))
if args == ["tool", "update-shell"]:
    raise SystemExit(int(os.environ.get("GETGO_FAKE_UPDATE_EXIT", "0")))
if len(args) == 3 and args[:2] == ["tool", "install"]:
    package = args[2]
    if package == os.environ.get("GETGO_FAKE_FAIL_PACKAGE"):
        print(f"fake uv: could not install {package}", file=sys.stderr)
        raise SystemExit(int(os.environ.get("GETGO_FAKE_FAIL_EXIT", "17")))
    print(f"installed {package}")
    raise SystemExit(0)
print(f"unexpected fake uv arguments: {args!r}", file=sys.stderr)
raise SystemExit(99)
""",
        encoding="utf-8",
        newline="\n",
    )

    if os.name == "nt":
        executable = bin_dir / "uv.cmd"
        executable.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        executable = bin_dir / "uv"
        _write_executable(executable, f"#!{sys.executable}\n" + script.read_text(encoding="utf-8"))

    env = {
        "GETGO_FAKE_UV_LOG": str(log),
        "GETGO_FAKE_TOOL_BIN": str(tool_bin),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PATH": os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))),
    }
    return FakeUv(executable, bin_dir, tool_bin, log, home, env)


@pytest.fixture
def fake_installer_factory(tmp_path: Path, fake_uv: FakeUv):
    def make(program: str, *, installer_exit: int = 0) -> tuple[Path, Path, dict[str, str]]:
        transport_dir = tmp_path / f"transport-{program}"
        transport_dir.mkdir()
        transport_log = tmp_path / f"{program}-calls.jsonl"

        if os.name == "nt":
            assert program == "powershell"
            helper = transport_dir / "fake_powershell.py"
            helper.write_text(
                """import json
import os
import shutil
import sys
from pathlib import Path

with open(os.environ["GETGO_FAKE_TRANSPORT_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")
if int(os.environ.get("GETGO_FAKE_INSTALL_EXIT", "0")):
    raise SystemExit(int(os.environ["GETGO_FAKE_INSTALL_EXIT"]))
target = Path(os.environ["USERPROFILE"]) / ".local" / "bin"
target.mkdir(parents=True, exist_ok=True)
shutil.copyfile(os.environ["GETGO_FAKE_UV_SOURCE"], target / "uv.cmd")
""",
                encoding="utf-8",
            )
            executable = transport_dir / "powershell.cmd"
            executable.write_text(f'@"{sys.executable}" "{helper}" %*\r\n', encoding="utf-8")
        else:
            assert program in {"curl", "wget"}
            executable = transport_dir / program
            payload = (
                "exit " + str(installer_exit)
                if installer_exit
                else "\n".join(
                    (
                        '/bin/mkdir -p "$HOME/.local/bin"',
                        '/bin/cp "$GETGO_FAKE_UV_SOURCE" "$HOME/.local/bin/uv"',
                        '/bin/chmod +x "$HOME/.local/bin/uv"',
                    )
                )
            )
            _write_executable(
                executable,
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "with open(os.environ['GETGO_FAKE_TRANSPORT_LOG'], 'a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\\\n')\n"
                f"print({payload!r})\n",
            )

        env = fake_uv.env.copy()
        env.update(
            {
                "PATH": str(transport_dir),
                "GETGO_FAKE_TRANSPORT_LOG": str(transport_log),
                "GETGO_FAKE_UV_SOURCE": str(fake_uv.executable),
                "GETGO_FAKE_INSTALL_EXIT": str(installer_exit),
            }
        )
        return executable, transport_log, env

    return make
