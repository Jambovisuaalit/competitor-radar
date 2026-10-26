"""Build a source ZIP from an explicit allowlist; never package local secrets."""
from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP_LEVEL_FILES = (
    ".env.example", ".gitignore", "CHANGELOG.md", "LICENSE", "README.md",
    "VERSION", "competitors.json", "main.py", "requirements.txt",
)


def build_release(version: str, *, root: Path = ROOT, output: Path | None = None) -> Path:
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise ValueError("Version must use MAJOR.MINOR.PATCH")
    if (root / "VERSION").read_text(encoding="utf-8").strip() != version:
        raise ValueError("Release version must match VERSION")
    destination = output if output is not None else root / "dist"
    destination.mkdir(parents=True, exist_ok=True)
    files = [root / name for name in TOP_LEVEL_FILES]
    for pattern in ("src/*.py", "tests/*.py", "scripts/*.py", ".github/workflows/*.yml"):
        files.extend(sorted(root.glob(pattern)))
    archive = destination / f"competitor-radar-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in files:
            if path.is_symlink() or not path.is_file():
                raise ValueError("Release inputs must be regular files")
            name = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(f"competitor-radar-{version}/{name}")
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / f"{archive.name}.sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8",
    )
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=(ROOT / "VERSION").read_text().strip())
    args = parser.parse_args()
    try:
        print(build_release(args.version))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Release build failed: {exc}\n")


if __name__ == "__main__":
    main()
