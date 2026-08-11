from __future__ import annotations

import json
import os
import shutil
import signal
from pathlib import Path

import pytest
from conftest import EntryPoint, FakeUv, run_getgo

USAGE = "Usage: getgo <package> [<package>...]\n"
HELP = USAGE + "Install one or more PyPI tools with uv.\n"
VERSION = "getgo 0.1.0\n"


def test_help_is_the_sole_help_argument(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    result = run_getgo(entrypoint, ["--help"], fake_uv.env)
    assert result.returncode == 0
    assert result.stdout == HELP
    assert result.stderr == ""
    assert fake_uv.calls() == []


def test_version_is_the_sole_version_argument(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    result = run_getgo(entrypoint, ["--version"], fake_uv.env)
    assert result.returncode == 0
    assert result.stdout == VERSION
    assert result.stderr == ""
    assert fake_uv.calls() == []


@pytest.mark.parametrize(
    "args",
    [[], ["--help", "ruff"], ["--version", "ruff"], ["--verbose"], ["-x"], ["bad name"], ["bad;name"], [".bad"]],
)
def test_usage_errors_never_invoke_uv(entrypoint: EntryPoint, fake_uv: FakeUv, args: list[str]) -> None:
    result = run_getgo(entrypoint, args, fake_uv.env)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.endswith(USAGE)
    assert fake_uv.calls() == []


def test_installs_each_package_once_in_order(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"PATH": os.pathsep.join((str(fake_uv.bin_dir), str(fake_uv.tool_bin)))}
    result = run_getgo(entrypoint, ["ruff", "pycowsay", "my_tool.pkg"], env)
    assert result.returncode == 0, result.stderr
    assert fake_uv.calls() == [
        ["tool", "install", "ruff"],
        ["tool", "install", "pycowsay"],
        ["tool", "install", "my_tool.pkg"],
        ["tool", "dir", "--bin"],
        ["tool", "update-shell"],
    ]


def test_first_failure_is_forwarded_and_stops(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"GETGO_FAKE_FAIL_PACKAGE": "broken", "GETGO_FAKE_FAIL_EXIT": "23"}
    result = run_getgo(entrypoint, ["broken", "must-not-run"], env)
    assert result.returncode == 23
    assert "fake uv: could not install broken" in result.stderr
    assert fake_uv.calls() == [["tool", "install", "broken"]]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal exit semantics")
def test_signal_termination_is_normalized(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"GETGO_FAKE_SIGNAL_PACKAGE": "signaled"}
    result = run_getgo(entrypoint, ["signaled", "must-not-run"], env)
    assert result.returncode == 128 + signal.SIGTERM
    assert fake_uv.calls() == [["tool", "install", "signaled"]]


def test_existing_uv_in_default_user_location_skips_bootstrap(
    entrypoint: EntryPoint, fake_uv: FakeUv, tmp_path: Path
) -> None:
    default_dir = fake_uv.home / ".local" / "bin"
    default_dir.mkdir(parents=True)
    target = default_dir / fake_uv.executable.name
    shutil.copyfile(fake_uv.executable, target)
    if os.name != "nt":
        target.chmod(0o755)
    env = fake_uv.env | {"PATH": ""}
    result = run_getgo(entrypoint, ["ruff"], env)
    assert result.returncode == 0, result.stderr
    assert fake_uv.calls()[:1] == [["tool", "install", "ruff"]]


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable permissions")
def test_directory_named_uv_does_not_shadow_executable(entrypoint: EntryPoint, fake_uv: FakeUv, tmp_path: Path) -> None:
    decoy_bin = tmp_path / "decoy-bin"
    (decoy_bin / "uv").mkdir(parents=True)
    env = fake_uv.env | {"PATH": os.pathsep.join((str(decoy_bin), str(fake_uv.bin_dir), str(fake_uv.tool_bin)))}
    result = run_getgo(entrypoint, ["ruff"], env)
    assert result.returncode == 0, result.stderr
    assert fake_uv.calls()[0] == ["tool", "install", "ruff"]


def test_update_shell_failure_cannot_fail_successful_installs(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"GETGO_FAKE_UPDATE_EXIT": "41"}
    result = run_getgo(entrypoint, ["ruff"], env)
    assert result.returncode == 0, result.stderr
    expected = (
        f'$env:Path = "{fake_uv.tool_bin};$env:Path"\n'
        if os.name == "nt"
        else f'export PATH="{fake_uv.tool_bin}:$PATH"\n'
    )
    assert result.stdout.endswith(expected)
    assert fake_uv.calls()[-2:] == [["tool", "dir", "--bin"], ["tool", "update-shell"]]


def test_no_hint_when_tool_bin_is_already_on_path(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"PATH": os.pathsep.join((str(fake_uv.bin_dir), str(fake_uv.tool_bin)))}
    result = run_getgo(entrypoint, ["ruff"], env)
    assert result.returncode == 0, result.stderr
    assert "export PATH=" not in result.stdout
    assert "$env:Path =" not in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="Unix installer selection")
@pytest.mark.parametrize(
    ("program", "expected_args"),
    [
        ("curl", ["-LsSf", "https://astral.sh/uv/install.sh"]),
        ("wget", ["-qO-", "https://astral.sh/uv/install.sh"]),
    ],
)
def test_unix_bootstrap_uses_official_installer(
    entrypoint: EntryPoint,
    fake_uv: FakeUv,
    fake_installer_factory,
    program: str,
    expected_args: list[str],
) -> None:
    _, transport_log, env = fake_installer_factory(program)
    result = run_getgo(entrypoint, ["ruff"], env)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in transport_log.read_text(encoding="utf-8").splitlines()]
    assert calls == [expected_args]
    assert fake_uv.calls()[0] == ["tool", "install", "ruff"]


@pytest.mark.skipif(os.name == "nt", reason="Unix installer selection")
def test_installer_failure_is_forwarded(entrypoint: EntryPoint, fake_uv: FakeUv, fake_installer_factory) -> None:
    _, _, env = fake_installer_factory("curl", installer_exit=29)
    result = run_getgo(entrypoint, ["ruff"], env)
    assert result.returncode == 29
    assert fake_uv.calls() == []


@pytest.mark.skipif(os.name != "nt", reason="Windows installer selection")
@pytest.mark.parametrize("program", ["powershell", "pwsh"])
def test_windows_bootstrap_uses_official_powershell_installer(
    entrypoint: EntryPoint, fake_uv: FakeUv, fake_installer_factory, program: str
) -> None:
    _, transport_log, env = fake_installer_factory(program)
    result = run_getgo(entrypoint, ["ruff"], env)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in transport_log.read_text(encoding="utf-8").splitlines()]
    assert calls == [["-ExecutionPolicy", "ByPass", "-c", "irm https://astral.sh/uv/install.ps1 | iex"]]
