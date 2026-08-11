from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from getgo import __version__

USAGE = "Usage: getgo <package> [<package>...]"
HELP = f"{USAGE}\nInstall one or more PyPI tools with uv."
UV_INSTALL_URL_UNIX = "https://astral.sh/uv/install.sh"
UV_INSTALL_URL_WINDOWS = "https://astral.sh/uv/install.ps1"
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def _usage_error(message: str) -> int:
    print(f"getgo: {message}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


def _parse_args(args: Sequence[str]) -> tuple[list[str] | None, int]:
    if list(args) == ["--help"]:
        print(HELP)
        return None, 0
    if list(args) == ["--version"]:
        print(f"getgo {__version__}")
        return None, 0
    if not args:
        return None, _usage_error("at least one package is required")
    for package in args:
        if package.startswith("-"):
            return None, _usage_error(f"unsupported option: {package}")
        if not PACKAGE_NAME.fullmatch(package):
            return None, _usage_error(f"invalid PyPI distribution name: {package}")
    return list(args), 0


def _is_executable(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def _default_uv_candidates() -> list[Path]:
    home_value = os.environ.get("USERPROFILE" if os.name == "nt" else "HOME")
    if not home_value:
        try:
            home_value = str(Path.home())
        except RuntimeError:
            return []
    binary_dir = Path(home_value) / ".local" / "bin"
    if os.name == "nt":
        return [binary_dir / name for name in ("uv.exe", "uv.com", "uv.cmd", "uv.bat", "uv")]
    return [binary_dir / "uv"]


def _find_uv() -> Path | None:
    found = shutil.which("uv")
    if found:
        candidate = Path(found).resolve()
        if _is_executable(candidate):
            return candidate
    for candidate in _default_uv_candidates():
        if _is_executable(candidate):
            return candidate.resolve()
    return None


def _run_unix_installer(downloader: str, arguments: list[str]) -> int:
    try:
        download = subprocess.Popen(
            [downloader, *arguments],
            stdout=subprocess.PIPE,
        )
        assert download.stdout is not None
        install = subprocess.Popen(["/bin/sh"], stdin=download.stdout)
        download.stdout.close()
        install_code = install.wait()
        download_code = download.wait()
    except OSError as error:
        print(f"getgo: failed to start uv installer: {error}", file=sys.stderr)
        return 1
    return download_code if download_code else install_code


def _bootstrap_uv() -> int:
    if os.name == "nt":
        powershell = shutil.which("powershell")
        if not powershell:
            print("getgo: PowerShell is required to install uv", file=sys.stderr)
            return 1
        try:
            return subprocess.run(
                [
                    powershell,
                    "-ExecutionPolicy",
                    "ByPass",
                    "-c",
                    f"irm {UV_INSTALL_URL_WINDOWS} | iex",
                ],
                check=False,
            ).returncode
        except OSError as error:
            print(f"getgo: failed to start uv installer: {error}", file=sys.stderr)
            return 1

    curl = shutil.which("curl")
    if curl:
        return _run_unix_installer(curl, ["-LsSf", UV_INSTALL_URL_UNIX])
    wget = shutil.which("wget")
    if wget:
        return _run_unix_installer(wget, ["-qO-", UV_INSTALL_URL_UNIX])
    print("getgo: curl or wget is required to install uv", file=sys.stderr)
    return 1


def _ensure_uv() -> tuple[Path | None, int]:
    uv = _find_uv()
    if uv is not None:
        return uv, 0
    result = _bootstrap_uv()
    if result:
        return None, result
    uv = _find_uv()
    if uv is None:
        print("getgo: the uv installer completed but uv could not be found", file=sys.stderr)
        return None, 1
    return uv, 0


def _run_uv(uv: Path, arguments: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [str(uv), *arguments],
            capture_output=capture,
            text=capture,
            check=False,
        )
    except OSError as error:
        print(f"getgo: failed to run uv: {error}", file=sys.stderr)
        return None


def _normalized_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path.strip('"'))).rstrip("/\\")


def _path_contains(directory: Path) -> bool:
    expected = _normalized_path(str(directory))
    return any(item and _normalized_path(item) == expected for item in os.environ.get("PATH", "").split(os.pathsep))


def _finish_path_setup(uv: Path) -> None:
    directory_result = _run_uv(uv, ["tool", "dir", "--bin"], capture=True)
    tool_bin: Path | None = None
    if directory_result is not None and directory_result.returncode == 0:
        output = directory_result.stdout.strip()
        if output:
            tool_bin = Path(output)

    # uv owns future-shell setup. This is intentionally best-effort: package
    # installation has already succeeded and a profile-edit failure must not
    # change that result.
    _run_uv(uv, ["tool", "update-shell"])

    if tool_bin is not None and not _path_contains(tool_bin):
        if os.name == "nt":
            print(f'$env:Path = "{tool_bin};$env:Path"')
        else:
            print(f'export PATH="{tool_bin}:$PATH"')


def run(args: Sequence[str]) -> int:
    packages, result = _parse_args(args)
    if packages is None:
        return result

    uv, result = _ensure_uv()
    if uv is None:
        return result

    for package in packages:
        completed = _run_uv(uv, ["tool", "install", package])
        if completed is None:
            return 1
        if completed.returncode:
            return completed.returncode

    _finish_path_setup(uv)
    return 0


def main() -> NoReturn:
    raise SystemExit(run(sys.argv[1:]))
