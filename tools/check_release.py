#!/usr/bin/env python3.11
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Validate an explicit public file list and optionally build a source archive."""
from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ACCOUNT_PATH = re.compile(r'/(?:users|home)/[^/\s]+/(?!\.\.)(?:[^\s"\']+)')
PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
BEARER = re.compile(r"(?:Bearer\s+|sk-[A-Za-z]+-)[A-Za-z0-9_./+-]{32,}")
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")


def release_files(root: Path) -> list[Path]:
    """Return the listed release files; reject unlisted files, symlinks, and private state."""
    manifest = root / "RELEASE_FILES.txt"
    names = [
        line.strip()
        for line in manifest.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate release file entries.")
    files = []
    for name in names:
        relative = Path(name)
        if relative.name in {
            "auth.json",
            ".credentials.json",
            ".env",
        } or relative.suffix in {".jsonl", ".sqlite", ".sif", ".db"}:
            raise ValueError(
                f"Private state and binary images are not release files: {name}"
            )
        # Hardware sources and libraries are allowed only as the example and the
        # bundled open library, never as private designs.
        if relative.suffix in {".v", ".sv", ".lib"} and relative.parts[0] not in {
            "examples",
            "tech",
        }:
            raise ValueError(
                f"RTL and library files are allowed only in examples/ and tech/: {name}"
            )
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Invalid release path: {name}")
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Release file is missing or a symlink: {name}")
        files.append(path)
    allowed = set(names)
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if ".git" in relative.parts or "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks are not allowed: {relative}")
        if path.is_file() and relative.as_posix() not in allowed:
            raise ValueError(f"Unexpected file outside release list: {relative}")
    return files


def validate(root: Path, deny_terms: list[str]) -> list[Path]:
    """Check the release files for private paths, credentials, and syntax errors."""
    files = release_files(root)
    findings = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        label = str(path.relative_to(root))
        for pattern in (LOCAL_ACCOUNT_PATH, PRIVATE_KEY, BEARER, JWT):
            if pattern.search(text):
                findings.append(
                    f"{label}: private account path or credential-like material"
                )
        for term in deny_terms:
            if re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])", text, re.I
            ):
                findings.append(f"{label}: excluded term found")
        if path.suffix == ".py":
            ast.parse(text, filename=label)
        if path.suffix == ".sh":
            result = subprocess.run(
                ["bash", "-n", str(path)], capture_output=True, text=True
            )
            if result.returncode:
                findings.append(f"{label}: shell syntax error: {result.stderr.strip()}")
    if findings:
        raise ValueError("\n".join(findings))
    return files


def main() -> int:
    """Validate the release and optionally write a source archive."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, help="Destination zip outside the source tree"
    )
    parser.add_argument(
        "--deny-file",
        type=Path,
        help="External text file of excluded identifiers, one per line; never bundled",
    )
    args = parser.parse_args()
    terms = args.deny_file.read_text().splitlines() if args.deny_file else []
    files = validate(ROOT, [term.strip() for term in terms if term.strip()])
    print(f"PASS: {len(files)} explicit public files; source and shell syntax checked.")
    if args.archive:
        destination = args.archive.expanduser().resolve()
        if destination.is_relative_to(ROOT):
            parser.error("Archive must be outside the source tree.")
        if destination.exists():
            parser.error("Archive already exists; choose a new destination.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(
            destination, "x", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for path in files:
                archive.write(path, f"ares/{path.relative_to(ROOT).as_posix()}")
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        print(f"Archive: {destination}\nSHA256: {digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, SyntaxError) as error:
        raise SystemExit(f"FAIL: {error}") from None
