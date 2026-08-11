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

USAGE = "Usage: getgo [--yes | --no-modify-path] <package> [<package>...]"
HELP = (
    f"{USAGE}\n"
    "Install PyPI packages as persistent uv tools with managed Python.\n"
    "  --yes             Add missing executable directories to future shells.\n"
    "  --no-modify-path  Never modify shell startup files or the user PATH."
)
UV_INSTALL_URL_UNIX = "https://astral.sh/uv/install.sh"
UV_INSTALL_URL_WINDOWS = "https://astral.sh/uv/install.ps1"
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
PATH_AUTO = "auto"
PATH_YES = "yes"
PATH_NO = "no"


def _normalize_returncode(returncode: int) -> int:
    return 128 + abs(returncode) if returncode < 0 else returncode


def _usage_error(message: str) -> int:
    print(f"getgo: {message}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_args(args: Sequence[str]) -> tuple[list[str] | None, str, int]:
    if list(args) == ["--help"]:
        print(HELP)
        return None, PATH_AUTO, 0
    if list(args) == ["--version"]:
        print(f"getgo {__version__}")
        return None, PATH_AUTO, 0
    if not args:
        return None, PATH_AUTO, _usage_error("at least one package is required")

    yes = _env_enabled("GETGO_YES")
    no_modify = _env_enabled("GETGO_NO_MODIFY_PATH")
    packages: list[str] = []
    for package in args:
        if package == "--yes":
            yes = True
            continue
        if package == "--no-modify-path":
            no_modify = True
            continue
        if package.startswith("-"):
            return None, PATH_AUTO, _usage_error(f"unsupported option: {package}")
        if not PACKAGE_NAME.fullmatch(package):
            return None, PATH_AUTO, _usage_error(f"invalid PyPI distribution name: {package}")
        packages.append(package)
    if yes and no_modify:
        return None, PATH_AUTO, _usage_error("--yes and --no-modify-path are mutually exclusive")
    if not packages:
        return None, PATH_AUTO, _usage_error("at least one package is required")
    return packages, PATH_YES if yes else PATH_NO if no_modify else PATH_AUTO, 0


def _is_executable(path: Path) -> bool:
    return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))


def _uv_binary_names() -> tuple[str, ...]:
    return ("uv.exe", "uv.com", "uv.cmd", "uv.bat", "uv") if os.name == "nt" else ("uv",)


def _candidate_uv_directories() -> list[Path]:
    directories: list[Path] = []
    for name in ("UV_INSTALL_DIR", "UV_UNMANAGED_INSTALL", "XDG_BIN_HOME"):
        if value := os.environ.get(name):
            directories.append(Path(value))
    if value := os.environ.get("XDG_DATA_HOME"):
        directories.append(Path(value).parent / "bin")

    home_value = os.environ.get("USERPROFILE" if os.name == "nt" else "HOME")
    if not home_value:
        try:
            home_value = str(Path.home())
        except RuntimeError:
            home_value = ""
    if home_value:
        directories.append(Path(home_value) / ".local" / "bin")
    return directories


def _default_uv_candidates() -> list[Path]:
    return [directory / name for directory in _candidate_uv_directories() for name in _uv_binary_names()]


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


def _installer_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["UV_NO_MODIFY_PATH"] = "1"
    return environment


def _run_unix_installer(downloader: str, arguments: list[str]) -> int:
    environment = _installer_environment()
    try:
        download = subprocess.Popen(
            [downloader, *arguments],
            stdout=subprocess.PIPE,
            env=environment,
        )
        assert download.stdout is not None
        try:
            install = subprocess.Popen(["/bin/sh"], stdin=download.stdout, env=environment)
        except OSError:
            download.stdout.close()
            if download.poll() is None:
                download.terminate()
            try:
                download.wait(timeout=5)
            except subprocess.TimeoutExpired:
                download.kill()
                download.wait()
            raise
        download.stdout.close()
        install_code = install.wait()
        download_code = download.wait()
    except OSError as error:
        print(f"getgo: failed to start uv installer: {error}", file=sys.stderr)
        return 1
    return _normalize_returncode(download_code if download_code else install_code)


def _bootstrap_uv() -> int:
    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            candidates = []
            if program_files := os.environ.get("PROGRAMFILES"):
                candidates.append(Path(program_files) / "PowerShell" / "7" / "pwsh.exe")
            if system_root := os.environ.get("SYSTEMROOT"):
                candidates.append(Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
            powershell = next((str(candidate) for candidate in candidates if candidate.is_file()), None)
        if not powershell:
            print("getgo: PowerShell is required to install uv", file=sys.stderr)
            return 1
        try:
            return _normalize_returncode(
                subprocess.run(
                    [
                        powershell,
                        "-ExecutionPolicy",
                        "ByPass",
                        "-c",
                        f"irm {UV_INSTALL_URL_WINDOWS} | iex",
                    ],
                    env=_installer_environment(),
                    check=False,
                ).returncode
            )
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


def _run_uv(
    uv: Path,
    arguments: list[str],
    *,
    capture: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        if capture:
            return subprocess.run(
                [str(uv), *arguments],
                stdout=subprocess.PIPE,
                text=True,
                env=env,
                check=False,
            )
        return subprocess.run(
            [str(uv), *arguments],
            env=env,
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


def _deduplicate_paths(paths: Sequence[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = _normalized_path(str(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _append_github_path(paths: Sequence[Path]) -> bool:
    destination_value = os.environ.get("GITHUB_PATH")
    if not destination_value:
        return False
    destination = Path(destination_value)
    try:
        existing = destination.read_text(encoding="utf-8").splitlines() if destination.exists() else []
        normalized = {_normalized_path(line) for line in existing if line}
        additions = [str(path) for path in paths if _normalized_path(str(path)) not in normalized]
        if additions:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("a", encoding="utf-8", newline="\n") as stream:
                if destination.exists() and destination.stat().st_size and not destination.read_bytes().endswith(b"\n"):
                    stream.write("\n")
                stream.write("\n".join(additions) + "\n")
        return True
    except (OSError, UnicodeError) as error:
        print(f"getgo: could not update GITHUB_PATH: {error}", file=sys.stderr)
        return False


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _write_getgo_environment(paths: Sequence[Path]) -> Path:
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "getgo"
    config_root.mkdir(parents=True, exist_ok=True)
    destination = config_root / "env"
    lines = ["# Generated by getgo. Safe to source more than once."]
    for path in reversed(paths):
        quoted = _shell_quote(str(path))
        lines.extend((f'case ":$PATH:" in *:{quoted}:*) ;;', f'  *) export PATH={quoted}:"$PATH" ;;', "esac"))
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return destination


def _append_source_once(profile: Path, environment: Path) -> None:
    marker = "# getgo PATH bootstrap"
    existing = profile.read_text(encoding="utf-8", errors="surrogateescape") if profile.exists() else ""
    if marker in existing:
        return
    profile.parent.mkdir(parents=True, exist_ok=True)
    separator = "" if not existing or existing.endswith("\n") else "\n"
    addition = f"{marker}\n[ -f {_shell_quote(str(environment))} ] && . {_shell_quote(str(environment))}\n"
    with profile.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(separator + addition)


def _configure_unix_path(paths: Sequence[Path]) -> bool:
    try:
        environment = _write_getgo_environment(paths)
        home = Path(os.environ.get("HOME", Path.home()))
        shell = Path(os.environ.get("SHELL", "sh")).name.lower()
        if shell == "fish":
            fish = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "fish" / "conf.d" / "getgo.fish"
            fish.parent.mkdir(parents=True, exist_ok=True)
            lines = ["# Generated by getgo. Safe to source more than once."]
            for path in paths:
                escaped = str(path).replace("'", "\\'")
                lines.append(f"fish_add_path --global --move '{escaped}'")
            fish.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        elif shell in {"zsh"}:
            _append_source_once(Path(os.environ.get("ZDOTDIR", home)) / ".zshenv", environment)
        elif shell in {"bash"}:
            login = next(
                (home / name for name in (".bash_profile", ".bash_login", ".profile") if (home / name).exists()),
                home / ".bash_profile",
            )
            _append_source_once(login, environment)
            _append_source_once(home / ".bashrc", environment)
        elif shell in {"ksh", "mksh"}:
            _append_source_once(home / ".profile", environment)
            _append_source_once(home / ".kshrc", environment)
        elif shell in {"csh", "tcsh"}:
            profile = home / ".cshrc"
            marker = "# getgo PATH bootstrap"
            existing = profile.read_text(encoding="utf-8", errors="surrogateescape") if profile.exists() else ""
            if marker not in existing:
                separator = "" if not existing or existing.endswith("\n") else "\n"
                additions = ""
                for path in reversed(paths):
                    value = str(path).replace('"', '\\"')
                    additions += (
                        f'if ( ":${{PATH}}:" !~ *":{value}:"* ) then\n  setenv PATH "{value}:${{PATH}}"\nendif\n'
                    )
                with profile.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(f"{separator}{marker}\n{additions}")
        elif shell in {"nu", "nushell"}:
            config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "nushell" / "env.nu"
            config.parent.mkdir(parents=True, exist_ok=True)
            marker = "# getgo PATH bootstrap"
            existing = config.read_text(encoding="utf-8", errors="surrogateescape") if config.exists() else ""
            if marker not in existing:
                separator = "" if not existing or existing.endswith("\n") else "\n"
                additions = "\n".join(
                    "\n".join(
                        (
                            f"if not ($env.PATH | any {{ |entry| $entry == {_shell_quote(str(path))} }}) {{",
                            f"  $env.PATH = ($env.PATH | prepend {_shell_quote(str(path))})",
                            "}",
                        )
                    )
                    for path in reversed(paths)
                )
                with config.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(f"{separator}{marker}\n{additions}\n")
        else:
            _append_source_once(home / ".profile", environment)
        return True
    except (OSError, UnicodeError) as error:
        print(f"getgo: could not update shell startup files: {error}", file=sys.stderr)
        return False


def _configure_windows_path(paths: Sequence[Path]) -> bool:
    try:
        test_path = os.environ.get("_GETGO_TEST_WINDOWS_PATH_FILE")
        if test_path:
            destination = Path(test_path)
            current = destination.read_text(encoding="utf-8") if destination.exists() else ""
            parts = [part for part in current.split(";") if part]
            known = {_normalized_path(part) for part in parts}
            additions = [str(path) for path in paths if _normalized_path(str(path)) not in known]
            if additions:
                destination.write_text(";".join([*additions, *parts]), encoding="utf-8")
            return True

        import ctypes
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                current, value_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, value_type = "", winreg.REG_EXPAND_SZ
            parts = [part for part in str(current).split(";") if part]
            known = {_normalized_path(part) for part in parts}
            additions = [str(path) for path in paths if _normalized_path(str(path)) not in known]
            if additions:
                winreg.SetValueEx(key, "Path", 0, value_type, ";".join([*additions, *parts]))
        if additions:
            result = ctypes.c_ulong()
            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)
            )
        return True
    except (OSError, UnicodeError, ValueError) as error:
        print(f"getgo: could not update the Windows user PATH: {error}", file=sys.stderr)
        return False


def _print_activation(paths: Sequence[Path]) -> None:
    if os.name != "nt":
        joined = ":".join(str(path) for path in paths)
        print(f'export PATH="{joined}:$PATH"')
        return

    joined = ";".join(str(path) for path in paths)
    git_paths: list[str] = []
    for path in paths:
        value = str(path).replace("\\", "/")
        if len(value) >= 3 and value[1:3] == ":/":
            value = f"/{value[0].lower()}/{value[3:]}"
        git_paths.append(value)
    print(f'PowerShell: $env:Path = "{joined};$env:Path"')
    print(f'Command Prompt: set "PATH={joined};%PATH%"')
    print(f'Git Bash: export PATH="{":".join(git_paths)}:$PATH"')


def _wants_path_setup(policy: str, paths: Sequence[Path]) -> bool:
    if policy == PATH_YES:
        return True
    if policy == PATH_NO:
        return False
    if not sys.stdin.isatty():
        return False
    joined = ", ".join(str(path) for path in paths)
    print(f"getgo: add {joined} to PATH for future shells? [Y/n] ", end="", file=sys.stderr, flush=True)
    try:
        answer = sys.stdin.readline().strip().lower()
    except OSError:
        return False
    return answer in {"", "y", "yes"}


def _finish_path_setup(uv: Path, policy: str) -> None:
    directory_result = _run_uv(uv, ["tool", "dir", "--bin"], capture=True)
    tool_bin: Path | None = None
    if directory_result is not None and directory_result.returncode == 0:
        output = directory_result.stdout.strip()
        if output:
            tool_bin = Path(output)

    candidates = [uv.parent]
    if tool_bin is not None:
        candidates.append(tool_bin)
    missing = [path for path in _deduplicate_paths(candidates) if not _path_contains(path)]
    if not missing:
        return

    if policy != PATH_NO and os.environ.get("GITHUB_PATH") and _append_github_path(missing):
        print("getgo: PATH updated for subsequent GitHub Actions steps", file=sys.stderr)
        _print_activation(missing)
        return

    if not _wants_path_setup(policy, missing):
        print("getgo: installed successfully, but an executable directory is not on PATH", file=sys.stderr)
        _print_activation(missing)
        return

    remaining = list(missing)
    tool_missing = tool_bin is not None and any(
        _normalized_path(str(path)) == _normalized_path(str(tool_bin)) for path in missing
    )
    if os.name != "nt":
        if tool_missing:
            # Let uv inspect its preferred shell path without letting its
            # unguarded startup line win. getgo writes the idempotent config.
            update_environment = os.environ.copy()
            update_environment["PATH"] = os.pathsep.join((str(tool_bin), update_environment.get("PATH", "")))
            _run_uv(uv, ["tool", "update-shell"], env=update_environment)
        configured = _configure_unix_path(remaining)
    else:
        if tool_missing:
            update = _run_uv(uv, ["tool", "update-shell"])
            if update is not None and update.returncode == 0:
                remaining = [
                    path for path in remaining if _normalized_path(str(path)) != _normalized_path(str(tool_bin))
                ]
        configured = not remaining or _configure_windows_path(remaining)
    if configured:
        print("getgo: PATH configured for future shells; open a new shell to use installed tools", file=sys.stderr)
    else:
        print("getgo: installed successfully, but automatic PATH setup failed", file=sys.stderr)
    _print_activation(missing)


def run(args: Sequence[str]) -> int:
    packages, path_policy, result = _parse_args(args)
    if packages is None:
        return result

    uv, result = _ensure_uv()
    if uv is None:
        return result

    for package in packages:
        completed = _run_uv(uv, ["tool", "install", "--managed-python", f"{package}@latest"])
        if completed is None:
            return 1
        if completed.returncode:
            return _normalize_returncode(completed.returncode)

    _finish_path_setup(uv, path_policy)
    return 0


def main() -> NoReturn:
    raise SystemExit(run(sys.argv[1:]))
