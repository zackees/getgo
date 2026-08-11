from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    artifact = ROOT / "dist" / "getgo"
    data = artifact.read_bytes()
    required = {
        "APE/PE header": b"MZqFpD",
        "x86_64 architecture": b"x86_64",
        "aarch64 architecture": b"aarch64",
        "pinned toolchain": b"clang-tool-chain 1.5.8",
    }
    missing = [name for name, marker in required.items() if marker not in data]
    if not data.startswith(required["APE/PE header"]):
        missing.append("APE/PE header at offset zero")
    if missing:
        raise SystemExit("invalid dist/getgo: " + ", ".join(missing))
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
    if 183 not in elf_machines or 0x8664 not in pe_machines:  # EM_AARCH64 and IMAGE_FILE_MACHINE_AMD64
        raise SystemExit(f"invalid dist/getgo: ELF machines {sorted(elf_machines)}, PE machines {sorted(pe_machines)}")
    print(f"validated fat APE/PE artifact: {artifact.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
