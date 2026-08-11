from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ROOT

SHELLS = {
    "bash": ["bash", "--noprofile", "--rcfile", "{home}/.bashrc", "-i", "-c"],
    "zsh": ["zsh", "-c"],
    "fish": ["fish", "-c"],
    "dash": ["dash", "-l", "-c"],
    "ash": ["busybox", "ash", "-l", "-c"],
    "ksh": ["ksh", "-l", "-c"],
    "tcsh": ["tcsh", "-c"],
}

SOURCE_TWICE = {
    "bash": '. "$HOME/.bashrc"; . "$HOME/.bashrc"',
    "zsh": '. "$HOME/.zshenv"; . "$HOME/.zshenv"',
    "fish": 'source "$HOME/.config/fish/conf.d/getgo.fish"; source "$HOME/.config/fish/conf.d/getgo.fish"',
    "dash": '. "$HOME/.profile"; . "$HOME/.profile"',
    "ash": '. "$HOME/.profile"; . "$HOME/.profile"',
    "ksh": '. "$HOME/.profile"; . "$HOME/.profile"',
    "tcsh": 'source "$HOME/.cshrc"; source "$HOME/.cshrc"',
}


@pytest.mark.integration
@pytest.mark.parametrize("shell", SHELLS)
def test_real_shell_can_find_installed_tool_after_path_setup(shell: str, tmp_path: Path) -> None:
    if os.environ.get("GETGO_RUN_SHELL_MATRIX") != "1":
        pytest.skip("set GETGO_RUN_SHELL_MATRIX=1 after installing the shell matrix")
    if os.name == "nt" or not sys_platform_is_linux():
        pytest.skip("the real Unix shell matrix runs on Linux")

    artifact = ROOT / "dist" / "getgo"
    assert artifact.is_file(), "build dist/getgo before running the shell matrix"

    home = tmp_path / "home with spaces"
    home.mkdir()
    tool_bin = tmp_path.parent / "shared tool bin"
    tool_dir = tmp_path.parent / "shared tool environments"
    tool_bin.mkdir(exist_ok=True)
    tool_dir.mkdir(exist_ok=True)
    shell_path = shutil.which("busybox" if shell == "ash" else shell)
    assert shell_path, f"{shell} must be installed by the CI shell-matrix job"

    environment = os.environ.copy()
    environment.pop("GITHUB_PATH", None)
    environment.update(
        {
            "HOME": str(home),
            "SHELL": "/bin/ash" if shell == "ash" else shell_path,
            "UV_TOOL_BIN_DIR": str(tool_bin),
            "UV_TOOL_DIR": str(tool_dir),
            "ZDOTDIR": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
        }
    )
    environment["PATH"] = os.pathsep.join(
        part for part in environment.get("PATH", "").split(os.pathsep) if Path(part) != tool_bin
    )

    install = subprocess.run(
        ["/bin/sh", str(artifact), "--yes", "ruff"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert install.returncode == 0, f"stdout:\n{install.stdout}\n\nstderr:\n{install.stderr}"

    command = [part.format(home=home) for part in SHELLS[shell]]
    if shell == "tcsh":
        print_path = 'echo "__PATH__${PATH}"'
    elif shell == "fish":
        print_path = "printf '__PATH__%s\\n' (string join : $PATH)"
    else:
        print_path = "printf '__PATH__%s\\n' \"$PATH\""
    shell_command = f"{SOURCE_TWICE[shell]}; {print_path}; which ruff; ruff --version"
    check = subprocess.run(
        [*command, shell_command],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert check.returncode == 0, f"stdout:\n{check.stdout}\n\nstderr:\n{check.stderr}"
    path_line = next(line for line in check.stdout.splitlines() if line.startswith("__PATH__"))
    path_entries = path_line.removeprefix("__PATH__").split(os.pathsep)
    assert path_entries.count(str(tool_bin)) == 1
    assert str(tool_bin / "ruff") in check.stdout
    assert "ruff " in check.stdout


def sys_platform_is_linux() -> bool:
    import sys

    return sys.platform.startswith("linux")
