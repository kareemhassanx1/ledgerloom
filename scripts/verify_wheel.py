"""Reject package payloads that differ from tracked sources in this checkout."""

from __future__ import annotations

import argparse
import subprocess
import zipfile
from collections import Counter
from glob import glob
from pathlib import Path


def verify_wheel(wheel: Path, repository: Path) -> list[str]:
    tracked = (
        subprocess.check_output(["git", "ls-files", "-z", "--", "src/ledgerloom"], cwd=repository)
        .decode("utf-8")
        .split("\0")
    )
    expected = {
        name.removeprefix("src/"): (repository / name).read_bytes() for name in tracked if name
    }
    if not expected:
        return ["no tracked package sources found"]
    with zipfile.ZipFile(wheel) as archive:
        duplicates = sorted(
            name for name, count in Counter(archive.namelist()).items() if count > 1
        )
        actual = {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith("ledgerloom/") and not name.endswith("/")
        }
    errors = [f"duplicate archive entry: {name}" for name in duplicates]
    errors.extend(
        f"unexpected package file: {name}" for name in sorted(actual.keys() - expected.keys())
    )
    errors.extend(
        f"missing package file: {name}" for name in sorted(expected.keys() - actual.keys())
    )
    for name in sorted(expected.keys() & actual.keys()):
        source, packaged = expected[name], actual[name]
        # Git's Windows checkout conversion is harmless for Python source text.
        if name.endswith(".py"):
            source = source.replace(b"\r\n", b"\n")
            packaged = packaged.replace(b"\r\n", b"\n")
        if source != packaged:
            errors.append(f"package content differs from tracked source: {name}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheels", nargs="+", help="wheel paths or glob patterns")
    arguments = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    paths: list[Path] = []
    for pattern in arguments.wheels:
        matches = sorted(glob(pattern))
        if not matches:
            parser.error(f"no wheels match: {pattern}")
        paths.extend(Path(match) for match in matches)
    failed = False
    for wheel in dict.fromkeys(paths):
        errors = verify_wheel(wheel, repository)
        for error in errors:
            print(f"{wheel.name}: {error}")
        if errors:
            failed = True
        else:
            print(f"{wheel.name}: package payload matches tracked sources")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
