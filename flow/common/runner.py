#!/usr/bin/env python3.11
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Stage runner of the hardware flow (copied into each work directory).

The runner executes five steps in order: RTL verification (simulation or
formal), Design Compiler synthesis, gate-level verification, gate-level
simulation for switching activity, and PrimeTime power analysis. It writes
results/output.csv with area, delay, and power.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import re
import subprocess
import time
from pathlib import Path

STEPS = ("verify", "syn", "verify_syn", "sim_syn", "pt")
SCALARS = (
    "DESIGN_NAME",
    "CLOCK_PORT",
    "TB_SIM",
    "VCD_INSTANCE_SUFFIX",
    "FREQUENCY_MHZ",
    "CLK_UNCERTAINTY_SETUP_NS",
    "CLK_UNCERTAINTY_HOLD_NS",
    "COMPILE_NO_AUTOUNGROUP",
    "COMPILE_RETIME",
    "COMPILE_CLOCK_GATING",
    "POWER_PERIOD_MODE",
    "POWER_PERIOD_MARGIN",
    "SEC_GOLDEN_FILE",
    "SEC_TOP",
    "SEC_RESET",
    "TECH_DIR",
    "DC_SHELL",
    "LC_SHELL",
    "PT_SHELL",
    "VLIB",
    "VLOG",
    "VSIM",
    "JASPERGOLD",
)
ARRAYS = (
    "RTL_FILES",
    "CLOCK_PORTS",
    "TB_VERIFY",
    "WORKLOADS",
    "RTL_PARAMETERS",
    "RTL_INCLUDE_DIRS",
    "SYNTH_LIBS",
    "SIM_MODELS",
    "SYNTHETIC_LIBS",
)
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


class FlowError(RuntimeError):
    """A flow step failed; the message names the step and its log."""
    pass


def tcl_quote(value: str | Path) -> str:
    """Quote one Tcl word, including paths containing spaces or substitutions."""
    text = str(value)
    for old, new in (
        ("\\", "\\\\"),
        ('"', '\\"'),
        ("$", "\\$"),
        ("[", "\\["),
        ("]", "\\]"),
        ("\n", "\\n"),
        ("\r", "\\r"),
    ):
        text = text.replace(old, new)
    return '"' + text + '"'


def render(template: str, replacements: dict[str, str]) -> str:
    """Replace named placeholders in one pass without interpreting replacement text."""
    pattern = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in replacements:
            raise FlowError(f"unfilled template setting: {name}")
        return replacements[name]

    return pattern.sub(replace, template)


def read_config(root: Path) -> dict:
    """Read the documented Bash scalars and arrays without splitting their values."""
    if not (root / "config.sh").is_file():
        raise FlowError("config.sh is missing")
    script = ["set -e", "source ./tech.sh", "source ./config.sh"]
    for name in SCALARS:
        script.append(f'printf "%s\\0%s\\0" "{name}" "${{{name}-}}"')
    for name in ARRAYS:
        script.append(
            f'for value in "${{{name}[@]}}"; do printf "%s\\0%s\\0" "{name}" "$value"; done'
        )
    proc = subprocess.run(
        ["bash", "-c", "\n".join(script)], cwd=root, capture_output=True
    )
    if proc.returncode:
        raise FlowError("cannot load settings: " + proc.stderr.decode(errors="replace"))
    fields = proc.stdout.decode().split("\0")[:-1]
    if len(fields) % 2:
        raise FlowError(
            "settings produced unexpected output; config.sh should only assign values"
        )
    config = {name: [] for name in ARRAYS}
    for name, value in zip(fields[::2], fields[1::2]):
        if name in ARRAYS:
            if value:
                config[name].append(value)
        elif name in SCALARS:
            config[name] = value
        else:
            raise FlowError("settings produced unexpected output")
    return config


def positive(value: str, name: str) -> float:
    """Parse a positive number from a setting."""
    try:
        number = float(value)
    except ValueError as exc:
        raise FlowError(f"{name} must be a positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise FlowError(f"{name} must be a positive number")
    return number


def nonnegative(value: str, name: str) -> float:
    """Parse a nonnegative number from a setting."""
    try:
        number = float(value)
    except ValueError as exc:
        raise FlowError(f"{name} must be a nonnegative number") from exc
    if not math.isfinite(number) or number < 0:
        raise FlowError(f"{name} must be a nonnegative number")
    return number


class Flow:
    """The five flow steps of one work directory: verify, syn, verify_syn, sim_syn, and pt."""
    def __init__(self, root: Path, config: dict, *, verify_mode: str = "simulation"):
        self.root = root
        self.c = config
        self.mode = verify_mode
        self.run_id = "run_001"
        self.top = config["DESIGN_NAME"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", self.top):
            raise FlowError("DESIGN_NAME must be an ordinary Verilog identifier")
        self.clocks = config["CLOCK_PORTS"] or (
            [config["CLOCK_PORT"]] if config["CLOCK_PORT"] else []
        )
        for clock in self.clocks:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", clock):
                raise FlowError("clock ports must be ordinary Verilog identifiers")
        self.period = 1000 / positive(
            config.get("FREQUENCY_MHZ") or "10000", "FREQUENCY_MHZ"
        )
        self.workloads = config["WORKLOADS"] or ["default"]
        for workload in self.workloads:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", workload):
                raise FlowError(
                    "workload names must contain letters, digits, underscores, or hyphens"
                )
        for parameter in config["RTL_PARAMETERS"]:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*=.+", parameter):
                raise FlowError("RTL_PARAMETERS entries must use NAME=VALUE")
        self.target_libraries: list[Path] = []

    def path(self, value: str | Path) -> Path:
        """Resolve a path relative to the work directory."""
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def directory(self, value: str) -> Path:
        """Create a directory in the work directory and return it."""
        path = self.root / value
        path.mkdir(parents=True, exist_ok=True)
        return path

    def report(self, value: str) -> Path:
        """Return a report path in the work directory and create its parent."""
        path = self.root / value
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def run_tool(
        self,
        tool: str,
        arguments: list[str],
        log_path: Path,
        *,
        reject_errors: bool = False,
    ) -> str:
        """Run one tool with its output in a log file; raise FlowError on a failure."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [self.c.get(tool) or tool.lower(), *arguments]
        print(f"[{self.run_id}] {tool}: {log_path.relative_to(self.root)}", flush=True)
        with log_path.open("w") as log:
            try:
                proc = subprocess.run(
                    command, cwd=self.root, stdout=log, stderr=subprocess.STDOUT
                )
            except OSError as exc:
                log.write(str(exc) + "\n")
                raise FlowError(f"cannot run {command[0]}: {exc}") from exc
        text = log_path.read_text(errors="replace")
        if proc.returncode:
            raise FlowError(
                f"{tool} exited with code {proc.returncode}; see {log_path}"
            )
        if reject_errors and re.search(
            r"^\s*Error:", text, re.MULTILINE | re.IGNORECASE
        ):
            raise FlowError(f"{tool} reported an error; see {log_path}")
        return text

    def rtl(self) -> list[Path]:
        """Return the RTL source files of the design."""
        files = [self.path(value) for value in self.c["RTL_FILES"]]
        if not files:
            raise FlowError("RTL_FILES must contain at least one source file")
        for path in files:
            if not path.is_file():
                raise FlowError(f"RTL source is missing: {path}")
        return files

    def technology_paths(self, key: str) -> list[Path]:
        """Return the configured technology files of one kind (libraries or models)."""
        base = self.path(self.c.get("TECH_DIR") or ".")
        paths = []
        for value in self.c[key]:
            path = Path(value)
            if not path.is_absolute():
                path = base / path
            if not path.is_file():
                raise FlowError(f"{key} file is missing: {path}")
            paths.append(path)
        if key in ("SYNTH_LIBS", "SIM_MODELS") and not paths:
            raise FlowError(f"configure {key} in tech.sh or RTL_FLOW_{key}")
        return paths

    def libraries(self) -> list[Path]:
        """Return the .db synthesis libraries; compile .lib files with Library Compiler once."""
        if self.target_libraries:
            return self.target_libraries
        for path in self.technology_paths("SYNTH_LIBS"):
            if path.suffix == ".db":
                self.target_libraries.append(path)
                continue
            if path.suffix != ".lib":
                raise FlowError(f"synthesis libraries must use .lib or .db: {path}")
            # The compiled library is cached by content, so each .lib compiles once.
            # Technology files are only read. RTL_FLOW_TECH_CACHE selects a cache
            # that all runs share; without it, the cache is in the work directory.
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
            shared = os.environ.get("RTL_FLOW_TECH_CACHE")
            cache = Path(shared) if shared else self.directory("technology_cache")
            cache.mkdir(parents=True, exist_ok=True)
            target = cache / f"{path.stem}_{digest}.db"
            if not target.is_file():
                partial = cache / f".{path.stem}_{digest}.{os.getpid()}.db"
                script = self.report(f"syn/logs/{self.run_id}/lc_{digest}.tcl")
                script.write_text(
                    "\n".join(
                        [
                            f"read_lib {tcl_quote(path)}",
                            "set libraries [get_libs *]",
                            'if {[sizeof_collection $libraries] != 1} { error "expected '
                            'one library per Liberty file" }',
                            f"write_lib [get_object_name $libraries] -format db -output "
                            f"{tcl_quote(partial)}",
                            "quit",
                            "",
                        ]
                    )
                )
                self.run_tool(
                    "LC_SHELL",
                    ["-f", str(script)],
                    self.report(f"syn/logs/{self.run_id}/lc_{digest}.log"),
                    reject_errors=True,
                )
                if not partial.is_file():
                    raise FlowError(f"Library Compiler did not create {partial}")
                # A rename is atomic, so a parallel run never reads a partial file.
                os.replace(partial, target)
            self.target_libraries.append(target)
        return self.target_libraries

    def clock_constraints(self, period: float) -> str:
        """Return the clock and zero I/O-delay constraints (a virtual clock without a clock port)."""
        commands = []
        if self.clocks:
            for index, clock in enumerate(self.clocks):
                name = "CORE_CLK" if index == 0 else f"CORE_CLK_{index}"
                commands.append(
                    f"create_clock [get_ports {tcl_quote(clock)}] -name {name} -period "
                    f"{period:.10g} -waveform [list 0 {period/2:.10g}]"
                )
            clocks = " ".join(tcl_quote(clock) for clock in self.clocks)
            inputs = (
                f"[remove_from_collection [all_inputs] [get_ports [list {clocks}]]]"
            )
        else:
            commands.append(f"create_clock -name CORE_CLK -period {period:.10g}")
            inputs = "[all_inputs]"
        commands.extend(
            [
                f"set_input_delay -clock CORE_CLK 0 {inputs}",
                "set_output_delay -clock CORE_CLK 0 [all_outputs]",
            ]
        )
        for mode in ("SETUP", "HOLD"):
            raw = self.c.get(f"CLK_UNCERTAINTY_{mode}_NS") or "0"
            try:
                uncertainty = float(raw)
            except ValueError as exc:
                raise FlowError(
                    "clock uncertainty must be a nonnegative number of nanoseconds"
                ) from exc
            if not math.isfinite(uncertainty) or uncertainty < 0:
                raise FlowError(
                    "clock uncertainty must be a nonnegative number of nanoseconds"
                )
            commands.append(
                f"set_clock_uncertainty {uncertainty:.10g} -{mode.lower()} [all_clocks]"
            )
        return "\n".join(commands)

    def synthesize(self) -> None:
        """Fill synth.tcl and run Design Compiler; check that the netlist and reports exist."""
        self.directory(f"syn/reports/{self.run_id}")
        self.directory(f"syn/outputs/{self.run_id}")
        libraries = " ".join(tcl_quote(path) for path in self.libraries())
        synthetic = " ".join(
            tcl_quote(path) for path in self.technology_paths("SYNTHETIC_LIBS")
        )
        reads = "\n".join(
            f"analyze -format sverilog {tcl_quote(path)}" for path in self.rtl()
        )
        flags = []
        for setting, flag in (
            ("COMPILE_CLOCK_GATING", "-gate_clock"),
            ("COMPILE_NO_AUTOUNGROUP", "-no_auto_ungroup"),
            ("COMPILE_RETIME", "-retime"),
        ):
            value = self.c.get(setting) or "false"
            if value not in ("true", "false"):
                raise FlowError(f"{setting} must be true or false")
            if value == "true":
                flags.append(flag)
        elaborate = f"elaborate {tcl_quote(self.top)}"
        if self.c["RTL_PARAMETERS"]:
            elaborate += " -parameters " + tcl_quote(",".join(self.c["RTL_PARAMETERS"]))
        include = ""
        if self.c["RTL_INCLUDE_DIRS"]:
            directories = " ".join(
                tcl_quote(self.path(value)) for value in self.c["RTL_INCLUDE_DIRS"]
            )
            include = f"set search_path [concat $search_path [list {directories}]]"
        fields = {
            "TARGET_LIBRARIES": libraries,
            "SYNTHETIC_LIBRARIES": synthetic,
            "INCLUDE_DIRECTORIES": include,
            "READ_RTL": reads,
            "ELABORATE": elaborate,
            "DESIGN_NAME": tcl_quote(self.top),
            "CLOCK_CONSTRAINTS": self.clock_constraints(self.period),
            "EXTRA_CONSTRAINTS": "",
            "COMPILE_FLAGS": " ".join(flags),
            "AREA_REPORT": tcl_quote(
                self.root / f"syn/reports/{self.run_id}/area_report.rpt"
            ),
            "TIMING_REPORT": tcl_quote(
                self.root / f"syn/reports/{self.run_id}/timing_report.rpt"
            ),
            "QOR_REPORT": tcl_quote(self.root / f"syn/reports/{self.run_id}/qor.rpt"),
            "NETLIST": tcl_quote(self.netlist()),
        }
        script = self.report(f"syn/logs/{self.run_id}/synth_filled.tcl")
        script.write_text(
            render((self.root / "syn/templates/synth.tcl").read_text(), fields)
        )
        self.run_tool(
            "DC_SHELL",
            ["-f", str(script)],
            script.parent / "syn.log",
            reject_errors=True,
        )
        if not self.netlist().is_file():
            raise FlowError("synthesis did not produce a mapped netlist")
        self.timing_ns()
        self.area()

    def netlist(self) -> Path:
        """Return the path of the mapped gate-level netlist."""
        return self.root / f"syn/outputs/{self.run_id}/{self.top}_mapped.v"

    def timing_ns(self) -> float:
        """Return the critical-path delay in ns from the QoR report."""
        path = self.root / f"syn/reports/{self.run_id}/qor.rpt"
        text = path.read_text() if path.is_file() else ""
        matches = re.findall(r"Critical Path Length:\s*(" + NUMBER + ")", text)
        if not matches:
            raise FlowError("critical-path delay is missing from the synthesis report")
        # Multiple clock groups may be reported; record the longest path.
        values = [nonnegative(value, "critical-path delay") for value in matches]
        return max(values)

    def area(self) -> float:
        """Return the total cell area from the area report."""
        path = self.root / f"syn/reports/{self.run_id}/area_report.rpt"
        text = path.read_text() if path.is_file() else ""
        match = re.search(r"Total cell area:\s*(" + NUMBER + ")", text)
        if not match:
            raise FlowError("total cell area is missing from the synthesis report")
        return nonnegative(match.group(1), "cell area")

    def power_period(self) -> float:
        """Return the power-simulation period: the delay times the margin, or the target period."""
        mode = self.c.get("POWER_PERIOD_MODE") or "critical_path"
        if mode == "target":
            return self.period
        if mode != "critical_path":
            raise FlowError("POWER_PERIOD_MODE must be critical_path or target")
        margin = positive(
            self.c.get("POWER_PERIOD_MARGIN") or "1.10", "POWER_PERIOD_MARGIN"
        )
        if margin < 1:
            raise FlowError("POWER_PERIOD_MARGIN must be at least 1")
        timing = self.timing_ns()
        # Preserve a reported zero delay while using the target period for simulation.
        return timing * margin if timing > 0 else self.period

    def fill_tb(
        self, name: str, workload: str, period: float, destination: Path
    ) -> Path:
        """Fill one testbench template with the clock period and the paths."""
        template = self.root / f"rtl/tb/templates/{name}.sv"
        if not template.is_file():
            raise FlowError(f"testbench template is missing: {template}")
        fields = {
            "CLK_HALF": f"{period/2:.10g}",
            "WORKLOAD": workload,
            "DIR_LOC_TEMP": self.run_id,
            "DESIGN_NAME": self.top,
            "PROJ_DIR": str(self.root),
        }
        for parameter in self.c["RTL_PARAMETERS"]:
            key, value = parameter.split("=", 1)
            fields[key] = value
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / f"{name}_{workload}.sv"
        path.write_text(render(template.read_text(), fields))
        return path

    def simulate(
        self, name: str, workload: str, *, gate: bool, power: bool = False
    ) -> str:
        """Compile and run one testbench on the RTL or the netlist; raise on a failure."""
        stage = "sim-syn" if power else ("verify_syn" if gate else "verify_rtl")
        logs = self.directory(f"{stage}/logs/{self.run_id}")
        period = self.power_period() if gate else 10.0
        tb = self.fill_tb(
            name,
            workload,
            period,
            self.directory(f"rtl/tb/debug/{stage}/{self.run_id}"),
        )
        if gate:
            if not self.netlist().is_file():
                raise FlowError("mapped netlist is missing; run synthesis first")
            sources = [*self.technology_paths("SIM_MODELS"), self.netlist()]
        else:
            sources = self.rtl()
        work = logs / f"work_{name}_{workload}"
        self.run_tool("VLIB", [str(work)], logs / f"vlib_{name}_{workload}.log")
        arguments = ["-sv", "-work", str(work)]
        arguments += [
            f"+incdir+{self.path(value)}" for value in self.c["RTL_INCLUDE_DIRS"]
        ]
        arguments += [*(str(path) for path in sources), str(tb)]
        self.run_tool("VLOG", arguments, logs / f"compile_{name}_{workload}.log")
        arguments = ["-c", "-lib", str(work), name]
        if power:
            arguments.append("-voptargs=+acc")
        if not gate:
            arguments += [
                f"-g/{name}/{self.top}0/{parameter}"
                for parameter in self.c["RTL_PARAMETERS"]
            ]
        arguments += ["-do", "run -all; quit -f"]
        text = self.run_tool("VSIM", arguments, logs / f"{stage}_{name}_{workload}.log")
        if "TIMEOUT" in text or re.search(
            r"^#?\s*\*\*\s+(?:Fatal|Error):", text, re.MULTILINE
        ):
            raise FlowError(f"simulation failed or timed out; see {logs}")
        if not power and "ALL TESTS PASSED" not in text:
            raise FlowError(f"functional verification failed; see {logs}")
        return text

    def verify(self) -> None:
        """Run the RTL equivalence testbench, or the formal check in SEC mode."""
        if self.mode == "sec":
            self.sec()
            return
        if not self.c["TB_VERIFY"]:
            raise FlowError("TB_VERIFY must list at least one verification testbench")
        for name in self.c["TB_VERIFY"]:
            for workload in self.workloads:
                self.simulate(name, workload, gate=False)

    def verify_gate(self) -> None:
        # RTL equivalence and gate-level simulation are separate verification stages.
        """Run the equivalence testbench on the gate-level netlist."""
        if not self.c["TB_VERIFY"]:
            raise FlowError(
                "gate verification requires TB_VERIFY; explicitly skip this stage if unavailable"
            )
        for name in self.c["TB_VERIFY"]:
            for workload in self.workloads:
                self.simulate(name, workload, gate=True)

    def power_simulation(self) -> None:
        """Simulate the netlist with the power testbench and write a VCD file."""
        if not self.c["TB_SIM"]:
            raise FlowError("TB_SIM must identify a gate-level power testbench")
        self.directory(f"sim-syn/vcd/{self.run_id}")
        for workload in self.workloads:
            self.simulate(self.c["TB_SIM"], workload, gate=True, power=True)
            if (
                not self.vcd(workload).is_file()
                or self.vcd(workload).stat().st_size == 0
            ):
                raise FlowError(
                    f"simulation did not produce switching activity: {self.vcd(workload)}"
                )

    def vcd(self, workload: str) -> Path:
        """Return the VCD path of one workload."""
        return self.root / f"sim-syn/vcd/{self.run_id}/{self.top}_{workload}.vcd"

    def power(self) -> None:
        """Run PrimeTime averaged power analysis on the VCD of each workload."""
        libraries = " ".join(tcl_quote(path) for path in self.libraries())
        period = self.power_period()
        for workload in self.workloads:
            if not self.vcd(workload).is_file():
                raise FlowError(f"switching activity is missing: {self.vcd(workload)}")
            fields = {
                "TARGET_LIBRARIES": libraries,
                "NETLIST": tcl_quote(self.netlist()),
                "DESIGN_NAME": tcl_quote(self.top),
                "CLOCK_CONSTRAINTS": self.clock_constraints(period),
                "EXTRA_CONSTRAINTS": "",
                "STRIP_PATH": tcl_quote(
                    f"{self.c['TB_SIM']}/{self.top}{self.c.get('VCD_INSTANCE_SUFFIX') or '0'}"
                ),
                "VCD_FILE": tcl_quote(self.vcd(workload)),
                "POWER_REPORT": tcl_quote(
                    self.report(f"pt/reports/{self.run_id}/power_{workload}.rpt")
                ),
                "POWER_HIERARCHY_REPORT": tcl_quote(
                    self.report(
                        f"pt/reports/{self.run_id}/power_hierarchy_{workload}.rpt"
                    )
                ),
            }
            script = self.report(f"pt/debug/{self.run_id}/power_{workload}.tcl")
            script.write_text(
                render((self.root / "pt/templates/power.tcl").read_text(), fields)
            )
            self.run_tool(
                "PT_SHELL",
                ["-f", str(script)],
                self.report(f"pt/terminal_logs/{self.run_id}/pt_power_{workload}.log"),
                reject_errors=True,
            )
            self.total_power(workload)

    def total_power(self, workload: str) -> float:
        """Return the total power in watts from a PrimeTime power report."""
        path = self.root / f"pt/reports/{self.run_id}/power_{workload}.rpt"
        text = path.read_text() if path.is_file() else ""
        match = re.search(r"^\s*Total\s+(.*)$", text, re.MULTILINE)
        if not match:
            raise FlowError(f"total power is missing: {path}")
        values = re.findall(NUMBER, re.sub(r"\([^)]*%[^)]*\)|\S*%", "", match.group(1)))
        if not values:
            raise FlowError(f"total power cannot be read: {path}")
        # Use the reported power unit when available; zero remains a valid result.
        unit = re.search(
            r"Power Units\s*(?:=|:)\s*(" + NUMBER + r")?\s*(pW|nW|uW|mW|W)\b",
            text,
            re.IGNORECASE,
        )
        scale = 1.0
        if unit:
            factor = float(unit.group(1) or "1")
            scale = (
                factor
                * {"pw": 1e-12, "nw": 1e-9, "uw": 1e-6, "mw": 1e-3, "w": 1}[
                    unit.group(2).lower()
                ]
            )
        value = float(values[-1]) * scale
        if not math.isfinite(value) or value < 0:
            raise FlowError(f"invalid power value: {path}")
        return value

    def sec(self) -> None:
        """Prove sequential equivalence of the RTL and the reference RTL with JasperGold."""
        golden = self.c.get("SEC_GOLDEN_FILE")
        if not golden or not self.path(golden).is_file():
            raise FlowError("SEC_GOLDEN_FILE must identify the reference RTL")
        rtl = self.rtl()
        reference = [self.path(golden), *rtl[1:]]
        top = self.c.get("SEC_TOP") or self.top
        commands = ["clear -all"]
        for mode, files in (("spec", reference), ("imp", rtl)):
            includes = " ".join(
                tcl_quote(self.path(value)) for value in self.c["RTL_INCLUDE_DIRS"]
            )
            command = f"check_sec -analyze -{mode} -sv"
            if includes:
                command += f" -incdir [list {includes}]"
            commands += [
                command + " " + " ".join(tcl_quote(path) for path in files),
                f"check_sec -elaborate -{mode} -top {tcl_quote(top)} -disable_auto_bbox",
            ]
        if self.c["RTL_PARAMETERS"]:
            raise FlowError(
                "formal equivalence with RTL_PARAMETERS requires a custom formal setup"
            )
        commands += ["check_sec -setup", "check_sec -map -auto"]
        if self.clocks:
            commands += ["clock " + " ".join(tcl_quote(clock) for clock in self.clocks)]
        if self.c.get("SEC_RESET"):
            commands += ["reset " + self.c["SEC_RESET"]]
        commands += ["check_sec -gen", "check_sec -prove -strategy proof"]
        script = self.report(f"sec_rtl/logs/sec_{self.top}.tcl")
        script.write_text("\n".join(commands) + "\n")
        text = self.run_tool(
            "JASPERGOLD",
            ["-sec", "-batch", "-tcl", str(script)],
            script.with_suffix(".log"),
        )
        if not re.search(r"^proven\s*$", text, re.MULTILINE):
            raise FlowError(
                f"formal equivalence was not proven; see {script.with_suffix('.log')}"
            )

    def write_results(self) -> None:
        """Write results/output.csv: area, delay, simulation period, and power per workload."""
        path = self.report("results/output.csv")
        fieldnames = [
            "run_id",
            "frequency_mhz",
            "area",
            "timing_ns",
            "simulation_period_ns",
        ]
        fieldnames += [f"power_{workload}" for workload in self.workloads]
        row = {
            "run_id": self.run_id,
            "frequency_mhz": 1000 / self.period,
            "area": self.area(),
            "timing_ns": self.timing_ns(),
            "simulation_period_ns": self.power_period(),
        }
        row.update(
            {
                f"power_{workload}": self.total_power(workload)
                for workload in self.workloads
            }
        )
        with path.open("w", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(row)


def main() -> int:
    """Run the selected flow steps and write status.txt and progress.txt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--skip-verify-syn", action="store_true")
    parser.add_argument("--start-step", choices=STEPS)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    status = root / "status.txt"
    progress = root / "progress.txt"
    status.write_text("RUNNING\n")
    progress.write_text("")
    (root / "verify_failures.txt").unlink(missing_ok=True)
    step = "setup"
    try:
        if args.verify_only and args.skip_verify:
            raise FlowError("--verify-only and --skip-verify cannot be combined")
        flow = Flow(
            root,
            read_config(root),
            verify_mode=os.environ.get("RTL_FLOW_VERIFY_MODE", "simulation"),
        )
        operations = {
            "verify": flow.verify,
            "syn": flow.synthesize,
            "verify_syn": flow.verify_gate,
            "sim_syn": flow.power_simulation,
            "pt": flow.power,
        }
        selected = ["verify"] if args.verify_only else list(STEPS)
        if args.start_step and not args.verify_only:
            selected = selected[STEPS.index(args.start_step) :]
        for step in selected:
            if step == "verify" and args.skip_verify:
                continue
            if step == "verify_syn" and args.skip_verify_syn:
                continue
            start = time.monotonic()
            operations[step]()
            line = f"[{step}] complete in {time.monotonic()-start:.1f} s\n"
            with progress.open("a") as output:
                output.write(line)
            print(line.strip(), flush=True)
        if not args.verify_only:
            step = "results"
            flow.write_results()
        status.write_text("COMPLETE\n")
        return 0
    except (FlowError, OSError, ValueError) as exc:
        message = f"[{step}] {exc}"
        print(message, flush=True)
        with progress.open("a") as output:
            output.write(message + "\n")
        (root / "verify_failures.txt").write_text(message + "\n")
        status.write_text(f"FAILED run_001 {step}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
