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
    source_tree: bool = True


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


@dataclass(frozen=True)
class NativeWindowsHelpers:
    uv: Path
    installer: Path


def _compile_csharp_executable(source: str, output: Path) -> None:
    source_path = output.with_suffix(".cs")
    source_path.write_text(source, encoding="utf-8", newline="\n")
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    assert powershell, "PowerShell is required to compile native Windows test helpers"
    environment = os.environ.copy()
    environment["GETGO_CSHARP_SOURCE"] = str(source_path)
    environment["GETGO_CSHARP_OUTPUT"] = str(output)
    subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Add-Type -Path $env:GETGO_CSHARP_SOURCE -OutputAssembly $env:GETGO_CSHARP_OUTPUT "
            "-OutputType ConsoleApplication",
        ],
        env=environment,
        check=True,
    )


@pytest.fixture(scope="session")
def native_windows_helpers(tmp_path_factory: pytest.TempPathFactory) -> NativeWindowsHelpers | None:
    if os.name != "nt":
        return None
    root = tmp_path_factory.mktemp("native-windows-helpers")
    uv = root / "fake-uv.exe"
    installer = root / "fake-installer.exe"
    _compile_csharp_executable(
        r"""
using System;
using System.IO;
using System.Text;

public static class FakeUv {
    private static int EnvInt(string name, int fallback) {
        string value = Environment.GetEnvironmentVariable(name);
        return String.IsNullOrEmpty(value) ? fallback : Int32.Parse(value);
    }

    private static string Quote(string value) {
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    private static void Log(string[] args) {
        string path = Environment.GetEnvironmentVariable("GETGO_FAKE_UV_LOG");
        string line = "[";
        for (int i = 0; i < args.Length; ++i) {
            if (i != 0) line += ",";
            line += Quote(args[i]);
        }
        File.AppendAllText(path, line + "]\n", new UTF8Encoding(false));
    }

    public static int Main(string[] args) {
        Log(args);
        if (args.Length == 4 && args[0] == "tool" && args[1] == "install" && args[2] == "--managed-python") {
            string packageSpec = args[3];
            string package = packageSpec.EndsWith("@latest", StringComparison.Ordinal)
                ? packageSpec.Substring(0, packageSpec.Length - "@latest".Length)
                : packageSpec;
            if (package == Environment.GetEnvironmentVariable("GETGO_FAKE_FAIL_PACKAGE")) {
                Console.Error.WriteLine("fake uv: could not install " + package);
                return EnvInt("GETGO_FAKE_FAIL_EXIT", 17);
            }
            Console.WriteLine("installed " + package);
            return 0;
        }
        if (args.Length == 3 && args[0] == "tool" && args[1] == "dir" && args[2] == "--bin") {
            Console.WriteLine(Environment.GetEnvironmentVariable("GETGO_FAKE_TOOL_BIN"));
            return EnvInt("GETGO_FAKE_DIR_EXIT", 0);
        }
        if (args.Length == 2 && args[0] == "tool" && args[1] == "update-shell") {
            return EnvInt("GETGO_FAKE_UPDATE_EXIT", 0);
        }
        Console.Error.WriteLine("unexpected fake uv arguments");
        return 99;
    }
}
""",
        uv,
    )
    _compile_csharp_executable(
        r"""
using System;
using System.IO;
using System.Text;

public static class FakeInstaller {
    private static string Quote(string value) {
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }

    public static int Main(string[] args) {
        string line = "[";
        for (int i = 0; i < args.Length; ++i) {
            if (i != 0) line += ",";
            line += Quote(args[i]);
        }
        File.AppendAllText(
            Environment.GetEnvironmentVariable("GETGO_FAKE_TRANSPORT_LOG"),
            line + "]\n",
            new UTF8Encoding(false)
        );
        int exitCode = Int32.Parse(Environment.GetEnvironmentVariable("GETGO_FAKE_INSTALL_EXIT") ?? "0");
        if (exitCode != 0) return exitCode;
        string target = Path.Combine(Environment.GetEnvironmentVariable("USERPROFILE"), ".local", "bin");
        Directory.CreateDirectory(target);
        File.Copy(Environment.GetEnvironmentVariable("GETGO_FAKE_UV_SOURCE"), Path.Combine(target, "uv.exe"), true);
        return 0;
    }
}
""",
        installer,
    )
    return NativeWindowsHelpers(uv=uv, installer=installer)


def _entrypoint_params() -> list[str]:
    value = os.environ.get("GETGO_ENTRYPOINTS", "python")
    return [part.strip() for part in value.split(",") if part.strip()]


@pytest.fixture(scope="session")
def ape_loader(tmp_path_factory: pytest.TempPathFactory) -> Path:
    artifact = ROOT / "dist" / "getgo"
    assert artifact.is_file(), "build dist/getgo before running APE conformance tests"
    cache = tmp_path_factory.mktemp("ape-loader")
    environment = os.environ.copy()
    environment["TMPDIR"] = str(cache)
    result = subprocess.run(
        ["/bin/sh", str(artifact), "--version"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    loaders = list(cache.glob(".ape-*"))
    assert len(loaders) == 1, f"expected one extracted APE loader, found {loaders}"
    return loaders[0]


@pytest.fixture(params=_entrypoint_params())
def entrypoint(request: pytest.FixtureRequest, tmp_path: Path) -> EntryPoint:
    if request.param == "python":
        return EntryPoint("python", (sys.executable, "-m", "getgo"))
    if request.param == "installed":
        command = os.environ.get("GETGO_INSTALLED_COMMAND")
        assert command, "set GETGO_INSTALLED_COMMAND to the wheel-installed getgo executable"
        return EntryPoint("installed", (command,), source_tree=False)
    if request.param == "ape":
        artifact = ROOT / "dist" / "getgo"
        assert artifact.is_file(), "build dist/getgo before running APE conformance tests"
        if os.name == "nt":
            windows_artifact = tmp_path / "getgo.com"
            shutil.copyfile(artifact, windows_artifact)
            return EntryPoint("ape", (str(windows_artifact),))
        artifact.chmod(artifact.stat().st_mode | stat.S_IXUSR)
        loader = request.getfixturevalue("ape_loader")
        return EntryPoint("ape", (str(loader), str(artifact)))
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
    if entrypoint.source_tree:
        old_pythonpath = merged.get("PYTHONPATH")
        merged["PYTHONPATH"] = source_path if not old_pythonpath else os.pathsep.join((source_path, old_pythonpath))
    else:
        merged.pop("PYTHONPATH", None)
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
def fake_uv(tmp_path: Path, native_windows_helpers: NativeWindowsHelpers | None) -> FakeUv:
    fake_root = tmp_path / "fake"
    bin_dir = fake_root / "bin"
    tool_bin = fake_root / "tools"
    home = tmp_path / "home"
    log = tmp_path / "uv-calls.jsonl"
    bin_dir.mkdir(parents=True)
    tool_bin.mkdir()
    home.mkdir()

    if os.name == "nt":
        assert native_windows_helpers is not None
        executable = bin_dir / "uv.exe"
        shutil.copyfile(native_windows_helpers.uv, executable)
    else:
        script = tmp_path / "fake_uv.py"
        script.write_text(
            """import json
import os
import signal
import sys

with open(os.environ["GETGO_FAKE_UV_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")

args = sys.argv[1:]
if args == ["tool", "dir", "--bin"]:
    print(os.environ["GETGO_FAKE_TOOL_BIN"])
    raise SystemExit(int(os.environ.get("GETGO_FAKE_DIR_EXIT", "0")))
if args == ["tool", "update-shell"]:
    raise SystemExit(int(os.environ.get("GETGO_FAKE_UPDATE_EXIT", "0")))
if len(args) == 4 and args[:3] == ["tool", "install", "--managed-python"]:
    package = args[3].removesuffix("@latest")
    if package == os.environ.get("GETGO_FAKE_SIGNAL_PACKAGE"):
        os.kill(os.getpid(), signal.SIGTERM)
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
def fake_installer_factory(tmp_path: Path, fake_uv: FakeUv, native_windows_helpers: NativeWindowsHelpers | None):
    def make(program: str, *, installer_exit: int = 0) -> tuple[Path, Path, dict[str, str]]:
        transport_dir = tmp_path / f"transport-{program}"
        transport_dir.mkdir()
        transport_log = tmp_path / f"{program}-calls.jsonl"

        if os.name == "nt":
            assert program in {"powershell", "pwsh"}
            assert native_windows_helpers is not None
            executable = transport_dir / f"{program}.exe"
            shutil.copyfile(native_windows_helpers.installer, executable)
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
                "    stream.write(json.dumps(sys.argv[1:]) + chr(10))\n"
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
