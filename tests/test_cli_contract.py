from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

import pytest
from conftest import ROOT, EntryPoint, FakeUv, run_getgo

USAGE = "Usage: getgo [--yes | --no-modify-path] <package> [<package>...]\n"
HELP = (
    USAGE
    + "Install PyPI packages as persistent uv tools with managed Python.\n"
    + "  --yes             Add missing executable directories to future shells.\n"
    + "  --no-modify-path  Never modify shell startup files or the user PATH.\n"
)
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
    [
        [],
        ["--help", "ruff"],
        ["--version", "ruff"],
        ["--verbose"],
        ["-x"],
        ["--yes", "--no-modify-path", "ruff"],
        ["bad name"],
        ["bad;name"],
        [".bad"],
    ],
)
def test_usage_errors_never_invoke_uv(entrypoint: EntryPoint, fake_uv: FakeUv, args: list[str]) -> None:
    result = run_getgo(entrypoint, args, fake_uv.env)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.endswith(USAGE)
    assert fake_uv.calls() == []


def test_installs_each_package_once_in_order_with_managed_python(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"PATH": os.pathsep.join((str(fake_uv.bin_dir), str(fake_uv.tool_bin)))}
    result = run_getgo(entrypoint, ["ruff", "pycowsay", "my_tool.pkg"], env)
    assert result.returncode == 0, result.stderr
    assert fake_uv.calls() == [
        ["tool", "install", "--managed-python", "ruff@latest"],
        ["tool", "install", "--managed-python", "pycowsay@latest"],
        ["tool", "install", "--managed-python", "my_tool.pkg@latest"],
        ["tool", "dir", "--bin"],
    ]


def test_first_failure_is_forwarded_and_stops(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"GETGO_FAKE_FAIL_PACKAGE": "broken", "GETGO_FAKE_FAIL_EXIT": "23"}
    result = run_getgo(entrypoint, ["broken", "must-not-run"], env)
    assert result.returncode == 23
    assert "fake uv: could not install broken" in result.stderr
    assert fake_uv.calls() == [["tool", "install", "--managed-python", "broken@latest"]]


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal exit semantics")
def test_signal_termination_is_normalized(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"GETGO_FAKE_SIGNAL_PACKAGE": "signaled"}
    result = run_getgo(entrypoint, ["signaled", "must-not-run"], env)
    assert result.returncode == 128 + signal.SIGTERM
    assert fake_uv.calls() == [["tool", "install", "--managed-python", "signaled@latest"]]


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
    assert fake_uv.calls()[:1] == [["tool", "install", "--managed-python", "ruff@latest"]]


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable permissions")
def test_directory_named_uv_does_not_shadow_executable(entrypoint: EntryPoint, fake_uv: FakeUv, tmp_path: Path) -> None:
    decoy_bin = tmp_path / "decoy-bin"
    (decoy_bin / "uv").mkdir(parents=True)
    env = fake_uv.env | {"PATH": os.pathsep.join((str(decoy_bin), str(fake_uv.bin_dir), str(fake_uv.tool_bin)))}
    result = run_getgo(entrypoint, ["ruff"], env)
    assert result.returncode == 0, result.stderr
    assert fake_uv.calls()[0] == ["tool", "install", "--managed-python", "ruff@latest"]


def test_update_shell_failure_cannot_fail_successful_installs(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"GETGO_FAKE_UPDATE_EXIT": "41"}
    result = run_getgo(entrypoint, ["--yes", "ruff"], env)
    assert result.returncode == 0, result.stderr
    expected = (
        f'PowerShell: $env:Path = "{fake_uv.tool_bin};$env:Path"'
        if os.name == "nt"
        else f'export PATH="{fake_uv.tool_bin}:$PATH"'
    )
    assert expected in result.stdout
    assert fake_uv.calls()[-2:] == [["tool", "dir", "--bin"], ["tool", "update-shell"]]


def test_noninteractive_install_never_changes_path_without_consent(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    result = run_getgo(entrypoint, ["ruff"], fake_uv.env)
    assert result.returncode == 0, result.stderr
    assert ["tool", "update-shell"] not in fake_uv.calls()
    assert "not on PATH" in result.stderr


def test_yes_explicitly_requests_path_setup(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    result = run_getgo(entrypoint, ["--yes", "ruff"], fake_uv.env)
    assert result.returncode == 0, result.stderr
    assert fake_uv.calls()[-1] == ["tool", "update-shell"]


def test_no_modify_path_never_requests_path_setup(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    result = run_getgo(entrypoint, ["--no-modify-path", "ruff"], fake_uv.env)
    assert result.returncode == 0, result.stderr
    assert ["tool", "update-shell"] not in fake_uv.calls()
    assert "not on PATH" in result.stderr


def test_github_path_is_updated_once_without_prompting(entrypoint: EntryPoint, fake_uv: FakeUv, tmp_path: Path) -> None:
    github_path = tmp_path / "github-path"
    github_path.write_text("existing-entry\n", encoding="utf-8")
    env = fake_uv.env | {"GITHUB_PATH": str(github_path)}

    first = run_getgo(entrypoint, ["ruff"], env)
    second = run_getgo(entrypoint, ["ruff"], env)

    assert first.returncode == second.returncode == 0
    lines = github_path.read_text(encoding="utf-8").splitlines()
    assert lines == ["existing-entry", str(fake_uv.tool_bin)]
    assert ["tool", "update-shell"] not in fake_uv.calls()


def test_no_modify_path_overrides_github_actions(entrypoint: EntryPoint, fake_uv: FakeUv, tmp_path: Path) -> None:
    github_path = tmp_path / "github-path"
    github_path.write_text("existing-entry\n", encoding="utf-8")
    env = fake_uv.env | {"GITHUB_PATH": str(github_path)}

    result = run_getgo(entrypoint, ["--no-modify-path", "ruff"], env)

    assert result.returncode == 0, result.stderr
    assert github_path.read_text(encoding="utf-8") == "existing-entry\n"
    assert ["tool", "update-shell"] not in fake_uv.calls()


@pytest.mark.skipif(os.name == "nt", reason="Unix shell startup files")
def test_ash_fallback_is_idempotent_and_preserves_profile(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    profile = fake_uv.home / ".profile"
    profile.write_text("# keep me\n", encoding="utf-8")
    env = fake_uv.env | {"SHELL": "/bin/ash", "GETGO_FAKE_UPDATE_EXIT": "2"}

    first = run_getgo(entrypoint, ["--yes", "ruff"], env)
    second = run_getgo(entrypoint, ["--yes", "ruff"], env)

    assert first.returncode == second.returncode == 0
    profile_text = profile.read_text(encoding="utf-8")
    assert profile_text.startswith("# keep me\n")
    assert profile_text.count("# getgo PATH bootstrap") == 1
    environment = fake_uv.home / ".config" / "getgo" / "env"
    assert environment.is_file()
    assert str(fake_uv.tool_bin) in environment.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="Unix shell startup files")
def test_non_utf8_profile_cannot_fail_a_successful_install(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    profile = fake_uv.home / ".profile"
    profile.write_bytes(b"# preserved\n\xff\n")
    env = fake_uv.env | {"SHELL": "/bin/ash", "GETGO_FAKE_UPDATE_EXIT": "2"}

    result = run_getgo(entrypoint, ["--yes", "ruff"], env)

    assert result.returncode == 0, result.stderr
    assert profile.read_bytes().startswith(b"# preserved\n\xff\n")
    assert b"# getgo PATH bootstrap" in profile.read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="Unix shell startup files")
@pytest.mark.parametrize(
    ("shell", "relative_files"),
    [
        ("sh", [".profile"]),
        ("dash", [".profile"]),
        ("bash", [".bash_profile", ".bashrc"]),
        ("zsh", [".zshenv"]),
        ("fish", [".config/fish/conf.d/getgo.fish"]),
        ("ksh", [".profile", ".kshrc"]),
        ("tcsh", [".cshrc"]),
        ("nu", [".config/nushell/env.nu"]),
    ],
)
def test_unix_fallback_targets_each_shell_idempotently(
    entrypoint: EntryPoint, fake_uv: FakeUv, shell: str, relative_files: list[str]
) -> None:
    env = fake_uv.env | {"SHELL": f"/usr/bin/{shell}", "GETGO_FAKE_UPDATE_EXIT": "2"}

    first = run_getgo(entrypoint, ["--yes", "ruff"], env)
    second = run_getgo(entrypoint, ["--yes", "ruff"], env)

    assert first.returncode == second.returncode == 0
    for relative_file in relative_files:
        content = (fake_uv.home / relative_file).read_text(encoding="utf-8")
        assert str(fake_uv.tool_bin) in content or "getgo/env" in content
        assert content.count("# getgo PATH bootstrap") <= 1


def test_uv_is_discovered_from_xdg_bin_home(entrypoint: EntryPoint, fake_uv: FakeUv, tmp_path: Path) -> None:
    xdg_bin = tmp_path / "xdg-bin"
    xdg_bin.mkdir()
    target = xdg_bin / fake_uv.executable.name
    shutil.copyfile(fake_uv.executable, target)
    if os.name != "nt":
        target.chmod(0o755)
    env = fake_uv.env | {"PATH": "", "XDG_BIN_HOME": str(xdg_bin)}

    result = run_getgo(entrypoint, ["--no-modify-path", "ruff"], env)

    assert result.returncode == 0, result.stderr
    assert fake_uv.calls()[0] == ["tool", "install", "--managed-python", "ruff@latest"]


def test_uv_is_discovered_from_xdg_data_sibling_bin(entrypoint: EntryPoint, fake_uv: FakeUv, tmp_path: Path) -> None:
    data_home = tmp_path / "share"
    xdg_bin = tmp_path / "bin"
    data_home.mkdir()
    xdg_bin.mkdir()
    target = xdg_bin / fake_uv.executable.name
    shutil.copyfile(fake_uv.executable, target)
    if os.name != "nt":
        target.chmod(0o755)
    env = fake_uv.env | {"PATH": "", "XDG_DATA_HOME": str(data_home)}

    result = run_getgo(entrypoint, ["--no-modify-path", "ruff"], env)

    assert result.returncode == 0, result.stderr
    assert fake_uv.calls()[0] == ["tool", "install", "--managed-python", "ruff@latest"]


def test_yes_environment_variable_requests_path_setup(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    result = run_getgo(entrypoint, ["ruff"], fake_uv.env | {"GETGO_YES": "true"})
    assert result.returncode == 0, result.stderr
    assert fake_uv.calls()[-1] == ["tool", "update-shell"]


def test_no_modify_environment_variable_wins_without_yes(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    result = run_getgo(entrypoint, ["ruff"], fake_uv.env | {"GETGO_NO_MODIFY_PATH": "1"})
    assert result.returncode == 0, result.stderr
    assert ["tool", "update-shell"] not in fake_uv.calls()


def test_distinct_uv_and_tool_directories_are_both_configured(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    uv_bin = fake_uv.home / ".local" / "bin"
    uv_bin.mkdir(parents=True)
    uv = uv_bin / fake_uv.executable.name
    shutil.copyfile(fake_uv.executable, uv)
    if os.name != "nt":
        uv.chmod(0o755)
    env = fake_uv.env | {"PATH": ""}

    result = run_getgo(entrypoint, ["--yes", "ruff"], env)

    assert result.returncode == 0, result.stderr
    if os.name == "nt":
        configured_path = Path(env["_GETGO_TEST_WINDOWS_PATH_FILE"])
        assert configured_path.is_file(), (result.stdout, result.stderr)
        configured = configured_path.read_text(encoding="utf-8")
    else:
        configured = (fake_uv.home / ".config" / "getgo" / "env").read_text(encoding="utf-8")
    assert str(uv_bin) in configured
    assert fake_uv.calls()[-1] == ["tool", "update-shell"]


def test_path_setup_failure_preserves_install_success(entrypoint: EntryPoint, fake_uv: FakeUv, tmp_path: Path) -> None:
    env = fake_uv.env | {"GETGO_FAKE_UPDATE_EXIT": "2"}
    if os.name == "nt":
        env["_GETGO_TEST_WINDOWS_PATH_FILE"] = str(tmp_path)
    else:
        blocked = tmp_path / "not-a-directory"
        blocked.write_text("blocked", encoding="utf-8")
        env["XDG_CONFIG_HOME"] = str(blocked)

    result = run_getgo(entrypoint, ["--yes", "ruff"], env)

    assert result.returncode == 0
    assert "automatic PATH setup failed" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows user PATH test seam")
def test_windows_path_fallback_is_idempotent(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    env = fake_uv.env | {"GETGO_FAKE_UPDATE_EXIT": "2"}

    first = run_getgo(entrypoint, ["--yes", "ruff"], env)
    second = run_getgo(entrypoint, ["--yes", "ruff"], env)

    assert first.returncode == second.returncode == 0
    configured_path = Path(env["_GETGO_TEST_WINDOWS_PATH_FILE"])
    assert configured_path.is_file(), (first.stdout, first.stderr, second.stdout, second.stderr)
    configured = configured_path.read_text(encoding="utf-8").split(";")
    assert configured.count(str(fake_uv.tool_bin)) == 1


@pytest.mark.skipif(os.name == "nt", reason="PTY prompt semantics are POSIX-specific")
@pytest.mark.parametrize(("answer", "updates"), [(b"n\n", 0), (b"\n", 1)])
def test_interactive_prompt_respects_refusal_and_default_yes(
    entrypoint: EntryPoint, fake_uv: FakeUv, answer: bytes, updates: int
) -> None:
    import pty

    environment = os.environ.copy()
    for name in ("GETGO_YES", "GETGO_NO_MODIFY_PATH", "GITHUB_PATH"):
        environment.pop(name, None)
    environment.update(fake_uv.env)
    if entrypoint.source_tree:
        environment["PYTHONPATH"] = str(ROOT / "src")
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            [*entrypoint.command, "ruff"],
            cwd=ROOT,
            env=environment,
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        os.close(slave)
        slave = -1
        os.write(master, answer)
        stdout, stderr = process.communicate(timeout=30)
    finally:
        if slave >= 0:
            os.close(slave)
        os.close(master)

    assert process.returncode == 0, (stdout, stderr)
    assert fake_uv.calls().count(["tool", "update-shell"]) == updates


@pytest.mark.skipif(os.name != "nt", reason="Windows activation commands")
def test_windows_hint_covers_powershell_cmd_and_git_bash(entrypoint: EntryPoint, fake_uv: FakeUv) -> None:
    result = run_getgo(entrypoint, ["--no-modify-path", "ruff"], fake_uv.env)
    assert result.returncode == 0, result.stderr
    assert "PowerShell:" in result.stdout
    assert "Command Prompt:" in result.stdout
    assert "Git Bash:" in result.stdout


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
    assert Path(env["GETGO_FAKE_INSTALL_ENV_LOG"]).read_text(encoding="utf-8") == "1"
    assert fake_uv.calls()[0] == ["tool", "install", "--managed-python", "ruff@latest"]


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
    assert Path(env["GETGO_FAKE_INSTALL_ENV_LOG"]).read_text(encoding="utf-8") == "1"
