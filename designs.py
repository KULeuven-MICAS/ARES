# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Load and validate an external RTL design registry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Design:
    """One registered design: top module, sources, and the ports with their roles."""
    key: str
    top: str
    src_files: list[str]
    clocks: list[str]
    resets: list[tuple[str, str]]
    inputs: list[tuple[str, int]]
    outputs: list[tuple[str, int]]
    lang: str = "v"
    extract_top: bool = False
    clock_gating: bool = True
    pt_hold: list[tuple[str, int]] = field(default_factory=list)
    notes: str = ""

    @property
    def primary_file(self) -> str:
        """Return the file name of the optimized source."""
        return Path(self.src_files[0]).name

    @property
    def combinational(self) -> bool:
        """Return True for a design without a clock."""
        return not self.clocks


DESIGNS_BY_KEY: dict[str, Design] = {}
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*\Z")
SAFE_KEY = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")


def load_designs(path: str | Path) -> dict[str, Design]:
    """Validate a JSON registry and resolve sources against its directory."""
    path = Path(path).resolve()
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or set(payload) != {"designs"}:
        raise ValueError("Configuration must contain only a 'designs' list.")
    if not isinstance(payload["designs"], list) or not payload["designs"]:
        raise ValueError("Register at least one design in 'designs'.")
    registry: dict[str, Design] = {}
    for item in payload["designs"]:
        if not isinstance(item, dict):
            raise ValueError("Each design must be a JSON object.")
        design = Design(**item)
        if not isinstance(design.key, str) or not SAFE_KEY.fullmatch(design.key):
            raise ValueError("Design keys must be safe directory names.")
        if design.key in registry:
            raise ValueError(f"Duplicate design key: {design.key}")
        if not isinstance(design.top, str) or not IDENTIFIER.fullmatch(design.top):
            raise ValueError(f"Invalid top module name for {design.key}.")
        if design.lang not in {"v", "sv"}:
            raise ValueError("lang must be 'v' or 'sv'.")
        for name in ("extract_top", "clock_gating"):
            if not isinstance(getattr(design, name), bool):
                raise ValueError(f"{name} must be a JSON boolean.")
        if not isinstance(design.src_files, list) or not design.src_files:
            raise ValueError(f"{design.key}: provide at least one source file.")
        if any(not isinstance(src, str) or not src for src in design.src_files):
            raise ValueError("Source filenames must be nonempty strings.")
        paths = [(path.parent / src).resolve() for src in design.src_files]
        if any(not src.is_file() for src in paths):
            raise ValueError(f"{design.key}: a source file is missing.")
        if len({src.name for src in paths}) != len(paths):
            raise ValueError(f"{design.key}: source basenames must be distinct.")
        design.src_files = [str(src) for src in paths]
        if not isinstance(design.clocks, list):
            raise ValueError("clocks must be a list of port names.")
        all_ports = list(design.clocks)
        for role in ("inputs", "outputs", "resets", "pt_hold"):
            pairs = getattr(design, role)
            if not isinstance(pairs, list):
                raise ValueError(f"{role} must be a list of pairs.")
            if any(
                not isinstance(pair, (list, tuple)) or len(pair) != 2 for pair in pairs
            ):
                raise ValueError(f"{role} entries must be [port, value] pairs.")
            setattr(design, role, [tuple(pair) for pair in pairs])
            if role != "pt_hold":
                all_ports.extend(name for name, _ in pairs)
        if any(
            not isinstance(name, str) or not IDENTIFIER.fullmatch(name)
            for name in all_ports
        ):
            raise ValueError("Ports must use ordinary Verilog identifiers.")
        if len(all_ports) != len(set(all_ports)):
            raise ValueError("Ports cannot appear in more than one role.")
        if not design.outputs:
            raise ValueError("Register at least one output to verify.")
        if any(
            type(width) is not int or width < 1
            for _, width in design.inputs + design.outputs
        ):
            raise ValueError("Port widths must be positive integer bit counts.")
        if any(polarity not in {"high", "low"} for _, polarity in design.resets):
            raise ValueError("Reset polarity must be 'high' or 'low'.")
        widths = dict(design.inputs)
        if len({name for name, _ in design.pt_hold}) != len(design.pt_hold):
            raise ValueError("pt_hold cannot repeat an input.")
        for name, value in design.pt_hold:
            if (
                name not in widths
                or type(value) is not int
                or not 0 <= value < (1 << widths[name])
            ):
                raise ValueError(
                    "pt_hold requires an input port and an unsigned value fitting its width."
                )
        registry[design.key] = design
    DESIGNS_BY_KEY.clear()
    DESIGNS_BY_KEY.update(registry)
    return DESIGNS_BY_KEY
