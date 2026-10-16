#!/usr/bin/env python3.11
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Testbench generation and flow control for the optimization loop.

The harness writes the SystemVerilog testbenches of a design (golden trace,
equivalence check, negative control, and power workload), builds the work
copy of the design, and runs the hardware flow through synthesis.py.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import synthesis
from designs import Design

FLOW_ROOT = Path(__file__).resolve().parent / "flow"
CLEAN_FRAMEWORK_TIMING = FLOW_ROOT / "timing"  # verification by simulation
CLEAN_FRAMEWORK_SEC = FLOW_ROOT / "sec"  # verification by formal equivalence


def sec_reset_expr(d: Design) -> str:
    """Return the JasperGold expression that is true while a reset is asserted."""
    terms = [p if pol == "high" else f"~{p}" for (p, pol) in d.resets]
    return " || ".join(terms)


# ---------------------------------------------------------------------------
# Stimulus settings (environment overrides; see docs/configuration.md)
# ---------------------------------------------------------------------------
# Counts are cycles for clocked designs and input vectors for combinational designs.
N_VECTORS_DEFAULT = int(os.environ.get("RTL_FLOW_VECTORS", "10000"))
N_PT_VECTORS = int(os.environ.get("RTL_FLOW_POWER_VECTORS", "4000"))
PT_WARMUP_CYCLES = int(os.environ.get("RTL_FLOW_POWER_WARMUP", "16"))
RESET_CYCLES = int(os.environ.get("RTL_FLOW_RESET_CYCLES", "8"))
# Zero disables random reset assertions during stimulus.
RESET_ASSERT_DIV = int(os.environ.get("RTL_FLOW_RESET_ASSERT_DIV", "32"))
SETTLE_NS = float(os.environ.get("RTL_FLOW_SETTLE_NS", "2.0"))
FREQ_MHZ = float(os.environ.get("RTL_FLOW_FREQUENCY_MHZ", "10000"))
SEED = int(os.environ.get("RTL_FLOW_SEED", "0xC0DE0042"), 0)


# ---------------------------------------------------------------------------
# Testbench building blocks
# ---------------------------------------------------------------------------
def _scratch_bits(d: Design) -> int:
    """Return the width of the random scratch register (at least the widest port)."""
    return max([32] + [w for _, w in d.inputs + d.outputs])


def _decl(name: str, width: int) -> str:
    """Declare one testbench signal."""
    return f"  logic {name};" if width == 1 else f"  logic [{width - 1}:0] {name};"


def _all_ports(d: Design) -> list[str]:
    """Return all port names in instance order."""
    return (
        list(d.clocks)
        + [r[0] for r in d.resets]
        + [n for (n, _) in d.inputs]
        + [n for (n, _) in d.outputs]
    )


def _dut_instance(d: Design) -> str:
    """Instantiate the design under test with all ports connected by name."""
    conns = ",\n".join(f"    .{p}({p})" for p in _all_ports(d))
    return f"  {d.top} {d.top}0 (\n{conns}\n  );"


def _signal_decls(d: Design, *, with_expected: bool) -> str:
    """Declare the port signals, the expected outputs, and the random scratch register."""
    lines: list[str] = []
    for c in d.clocks:
        lines.append(_decl(c, 1))
    for r, _ in d.resets:
        lines.append(_decl(r, 1))
    for n, w in d.inputs:
        lines.append(_decl(n, w))
    for n, w in d.outputs:
        lines.append(_decl(n, w))
    if with_expected:
        for n, w in d.outputs:
            lines.append(_decl(f"exp_{n}", w))
    lines.append(f"  logic [{_scratch_bits(d) - 1}:0] _rnd;")
    return "\n".join(lines)


def _clock_blocks(d: Design) -> str:
    """Toggle every clock with a period of 2*CLK_HALF."""
    return "\n".join(f"  always #(CLK_HALF) {c} = ~{c};" for c in d.clocks)


def _drive_inputs_task(d: Design) -> str:
    """Return the task that drives random values on all inputs and random resets."""
    body = ["  task automatic drive_inputs();"]
    for n, w in d.inputs:
        nch = (w + 31) // 32
        body.append("    _rnd = '0;")
        body.append(
            f"    for (int _i = 0; _i < {nch}; _i++) _rnd = (_rnd << 32) | $urandom();"
        )
        body.append(f"    {n} = _rnd[0];" if w == 1 else f"    {n} = _rnd[{w - 1}:0];")
    if RESET_ASSERT_DIV and d.resets:
        for r, pol in d.resets:
            asserted = "1'b1" if pol == "high" else "1'b0"
            deasserted = "1'b0" if pol == "high" else "1'b1"
            body.append(
                f"    {r} = (($urandom % {RESET_ASSERT_DIV}) == 0) ? {asserted} : {deasserted};"
            )
    body.append("  endtask")
    return "\n".join(body)


def _reset_apply(d: Design, level: str) -> str:
    """Assert or release all resets."""
    out = []
    for r, pol in d.resets:
        if level == "assert":
            val = "1'b1" if pol == "high" else "1'b0"
        else:
            val = "1'b0" if pol == "high" else "1'b1"
        out.append(f"    {r} = {val};")
    return "\n".join(out)


def _out_names(d: Design) -> list[str]:
    """Return the output port names."""
    return [n for (n, _) in d.outputs]


def _fwrite_fmt(d: Design) -> str:
    """Return the $fwrite format: one hexadecimal field per output."""
    return " ".join("%h" for _ in d.outputs)


def _stimulus_preamble(d: Design) -> str:
    """Seed the random generator, clear the inputs, and apply the reset sequence."""
    lines = [f"    _seed = 32'd{SEED};", "    void'($urandom(_seed));"]
    for c in d.clocks:
        lines.append(f"    {c} = 1'b0;")
    for n, _w in d.inputs:
        lines.append(f"    {n} = '0;")
    if d.resets and (not d.combinational):
        lines.append("    // assert reset")
        lines.append(_reset_apply(d, "assert"))
        lines.append(f"    repeat (RESET_CYCLES) @(negedge {d.clocks[0]});")
        lines.append("    // release reset")
        lines.append(_reset_apply(d, "deassert"))
    elif d.resets:
        lines.append(_reset_apply(d, "deassert"))
    return "\n".join(lines)


def _common_head(d: Design, module_name: str, clk_half: str) -> str:
    """Return the testbench header: declarations, clocks, the design, and the input task."""
    return "\n".join(
        [
            "`timescale 1ns/1ps",
            f"module {module_name};",
            f"  localparam real CLK_HALF = {clk_half};",
            f"  localparam real SETTLE_NS = {SETTLE_NS};",
            "  localparam int  N = {N};",
            f"  localparam int  RESET_CYCLES = {RESET_CYCLES};",
            "  integer _fd, _rc, _seed;",
            _signal_decls(
                d, with_expected=module_name.startswith(("tb_verify", "tb_negctrl"))
            ),
            _clock_blocks(d),
            _dut_instance(d),
            _drive_inputs_task(d),
        ]
    )


def _watchdog(d: Design) -> str:
    """Stop the simulation with TIMEOUT when it runs too long."""
    if d.combinational:
        return '  initial begin #((N + 200) * SETTLE_NS * 4); $display("TIMEOUT"); $finish; end'
    return '  initial begin #(CLK_HALF * 2 * (N + RESET_CYCLES + 200)); $display("TIMEOUT"); $finish; end'


# ---------------------------------------------------------------------------
# Testbenches
# ---------------------------------------------------------------------------
def gen_tb_gold(d: Design, golden_abspath: str) -> str:
    """Return the testbench that records the baseline outputs (the golden trace)."""
    head = _common_head(d, f"tb_gold_{d.key}", "5.0")
    pre = _stimulus_preamble(d)
    fmt = _fwrite_fmt(d)
    outs = ", ".join(_out_names(d))
    if d.combinational:
        loop = "\n".join(
            [
                "    for (int _k = 0; _k < N; _k++) begin",
                "      drive_inputs();",
                "      #(SETTLE_NS);",
                f'      $fwrite(_fd, "{fmt}\\n", {outs});',
                "    end",
            ]
        )
    else:
        clk0 = d.clocks[0]
        loop = "\n".join(
            [
                f"    @(negedge {clk0}); drive_inputs();",
                "    for (int _k = 0; _k < N; _k++) begin",
                f"      @(negedge {clk0});",
                f'      $fwrite(_fd, "{fmt}\\n", {outs});',
                "      drive_inputs();",
                "    end",
            ]
        )
    body = "\n".join(
        [
            "  initial begin",
            pre,
            f'    _fd = $fopen("{golden_abspath}", "w");',
            '    if (_fd == 0) begin $display("GOLDEN_OPEN_FAIL"); $finish; end',
            loop,
            "    $fclose(_fd);",
            '    $display("GOLDEN_DONE %0d", N);',
            "    $finish;",
            "  end",
            _watchdog(d),
            "endmodule",
        ]
    )
    return head + "\n" + body + "\n"


def _verify_module(
    d: Design, module_name: str, golden_abspath: str, clk_half: str
) -> str:
    """Return a testbench that compares the outputs cycle by cycle with a golden trace."""
    head = _common_head(d, module_name, clk_half)
    pre = _stimulus_preamble(d)
    nout = len(d.outputs)
    scan_fmt = " ".join("%h" for _ in d.outputs)
    exp_list = ", ".join(f"exp_{n}" for (n, _) in d.outputs)
    cmp_lines = []
    for n, w in d.outputs:
        cmp_lines.append(
            f'        if (_out_mismatch({n}, exp_{n}, {w})) begin _fail++; if (_fail <= 10) $display("MISMATCH cyc=%0d {n} dut=%h exp=%h", _k, {n}, exp_{n}); end'
        )
    cmp = "\n".join(cmp_lines)
    if d.combinational:
        loop = "\n".join(
            [
                "    for (int _k = 0; _k < N; _k++) begin",
                "      drive_inputs();",
                "      #(SETTLE_NS);",
                f'      _rc = $fscanf(_fd, "{scan_fmt}", {exp_list});',
                f'      if (_rc != {nout}) begin $display("GOLDEN_SHORT cyc=%0d rc=%0d", _k, _rc); _fail++; break; end',
                cmp,
                "    end",
            ]
        )
    else:
        clk0 = d.clocks[0]
        loop = "\n".join(
            [
                f"    @(negedge {clk0}); drive_inputs();",
                "    for (int _k = 0; _k < N; _k++) begin",
                f"      @(negedge {clk0});",
                f'      _rc = $fscanf(_fd, "{scan_fmt}", {exp_list});',
                f'      if (_rc != {nout}) begin $display("GOLDEN_SHORT cyc=%0d rc=%0d", _k, _rc); _fail++; break; end',
                cmp,
                "      drive_inputs();",
                "    end",
            ]
        )
    body = "\n".join(
        [
            f"  function automatic bit _out_mismatch(input logic [{_scratch_bits(d) - 1}:0] dut, input logic [{_scratch_bits(d) - 1}:0] exp, input int w);",
            "    for (int _b = 0; _b < w; _b++)",
            "      if (exp[_b] !== 1'bx && exp[_b] !== 1'bz && dut[_b] !== exp[_b]) return 1'b1;",
            "    return 1'b0;",
            "  endfunction",
            "  integer _fail;",
            "  initial begin",
            "    _fail = 0;",
            pre,
            f'    _fd = $fopen("{golden_abspath}", "r");',
            '    if (_fd == 0) begin $display("GOLDEN_OPEN_FAIL"); $finish; end',
            loop,
            "    $fclose(_fd);",
            '    $display("--------------------------------------------------");',
            '    if (_fail == 0) $display("ALL TESTS PASSED");',
            '    else $display("FAILURES DETECTED (%0d mismatches)", _fail);',
            '    $display("--------------------------------------------------");',
            "    $finish;",
            "  end",
            _watchdog(d),
            "endmodule",
        ]
    )
    return head + "\n" + body + "\n"


def gen_tb_verify(d: Design, golden_abspath: str) -> str:
    """Return the equivalence testbench; the flow runs it on the RTL and on the netlist."""
    return _verify_module(d, f"tb_verify_{d.key}", golden_abspath, "{CLK_HALF}")


def gen_tb_negctrl(d: Design, shifted_golden_abspath: str) -> str:
    """Return the negative-control testbench, which reads the shifted golden trace."""
    return _verify_module(d, f"tb_negctrl_{d.key}", shifted_golden_abspath, "5.0")


def gen_tb_pt(d: Design) -> str:
    """Return the power testbench: warm-up cycles, then a VCD dump of N_PT cycles."""
    module = f"tb_pt_{d.key}"
    head = "\n".join(
        [
            "`timescale 1ns/1ps",
            f"module {module};",
            "  localparam real CLK_HALF = {CLK_HALF};",
            f"  localparam real SETTLE_NS = {SETTLE_NS};",
            f"  localparam int  N_PT = {N_PT_VECTORS};",
            f"  localparam int  PT_WARMUP_CYCLES = {PT_WARMUP_CYCLES};",
            f"  localparam int  RESET_CYCLES = {RESET_CYCLES};",
            "  integer _seed;",
            _signal_decls(d, with_expected=False),
            _clock_blocks(d),
            _dut_instance(d),
            _drive_inputs_task(d),
        ]
    )
    pre = _stimulus_preamble(d)
    hold = " ".join(f"{p} = {v};" for (p, v) in getattr(d, "pt_hold", []) or [])
    hold_sp = " " + hold if hold else ""
    dump = f'    $dumpfile("{{PROJ_DIR}}/sim-syn/vcd/{{DIR_LOC_TEMP}}/{{DESIGN_NAME}}_{{WORKLOAD}}.vcd");\n    $dumpvars(0, {module});\n    $dumpon;'
    if d.combinational:
        warmup = ""
        loop = f"    for (int _k = 0; _k < N_PT; _k++) begin drive_inputs();{hold_sp} #(SETTLE_NS); end"
        watchdog = '  initial begin #((N_PT + 200) * SETTLE_NS * 4); $display("tb_pt TIMEOUT"); $finish; end'
    else:
        clk0 = d.clocks[0]
        warmup = f"    for (int _k = 0; _k < PT_WARMUP_CYCLES; _k++) begin @(negedge {clk0}); drive_inputs();{hold_sp} end"
        loop = f"    for (int _k = 0; _k < N_PT; _k++) begin @(negedge {clk0}); drive_inputs();{hold_sp} end"
        watchdog = '  initial begin #(CLK_HALF * 2 * (N_PT + PT_WARMUP_CYCLES + RESET_CYCLES + 200)); $display("tb_pt TIMEOUT"); $finish; end'
    body = "\n".join(
        [
            "  initial begin",
            pre,
            warmup,
            dump,
            loop,
            "    $dumpoff;",
            "    $finish;",
            "  end",
            watchdog,
            "endmodule",
        ]
    )
    return head + "\n" + body + "\n"


# ---------------------------------------------------------------------------
# Work copy of a design
# ---------------------------------------------------------------------------
def gen_config_sh(
    d: Design, rtl_basenames: list[str], sec_golden_file: str | None = None
) -> str:
    """Create one run configuration; technology and tool paths come from tech.sh."""
    q = shlex.quote
    lines = [
        "#!/usr/bin/env bash",
        "# Generated design configuration.",
        f"DESIGN_NAME={q(d.top)}",
        f'CLOCK_PORT={q(d.clocks[0] if d.clocks else "")}',
        "CLOCK_PORTS=(" + " ".join(q(c) for c in d.clocks) + ")",
        "RTL_FILES=(" + " ".join(q("rtl/src/" + b) for b in rtl_basenames) + ")",
        f'TB_SIM={q("tb_pt_" + d.key)}',
        "VCD_INSTANCE_SUFFIX=0",
        "TB_VERIFY=(" + q("tb_verify_" + d.key) + ")",
        f"FREQUENCY_MHZ={FREQ_MHZ}",
        "CLK_UNCERTAINTY_SETUP_NS=${RTL_FLOW_CLK_UNCERTAINTY_SETUP_NS:-0}",
        "CLK_UNCERTAINTY_HOLD_NS=${RTL_FLOW_CLK_UNCERTAINTY_HOLD_NS:-0}",
        "COMPILE_NO_AUTOUNGROUP=${RTL_FLOW_NO_AUTOUNGROUP:-false}",
        "COMPILE_RETIME=${RTL_FLOW_RETIME:-false}",
        f'COMPILE_CLOCK_GATING=${{RTL_FLOW_CLOCK_GATING:-{"true" if d.clock_gating else "false"}}}',
        "POWER_PERIOD_MODE=${RTL_FLOW_POWER_PERIOD_MODE:-critical_path}",
        "POWER_PERIOD_MARGIN=${RTL_FLOW_POWER_PERIOD_MARGIN:-1.10}",
        "RTL_PARAMETERS=()",
        "RTL_INCLUDE_DIRS=()",
        "WORKLOADS=(default)",
    ]
    if sec_golden_file:
        lines += [
            f"SEC_GOLDEN_FILE={q(sec_golden_file)}",
            f"SEC_TOP={q(d.top)}",
            f"SEC_RESET={q(sec_reset_expr(d))}",
        ]
    return "\n".join(lines) + "\n"


def extract_module(text: str, module_name: str) -> str:
    """Extract one module while ignoring keywords inside comments and strings."""
    ignored = re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"')
    code = ignored.sub(lambda match: re.sub(r"[^\n]", " ", match.group()), text)
    header = rf"\bmodule\s+(?:(?:automatic|static)\s+)?{re.escape(module_name)}(?![A-Za-z0-9_$])"
    m = re.search(header, code)
    if not m:
        raise ValueError(f"module '{module_name}' not found for extraction")
    start = m.start()
    em = re.search(
        r"\bendmodule\b(?:[ \t]*:[ \t]*[A-Za-z_][A-Za-z0-9_$]*)?", code[start:]
    )
    if not em:
        raise ValueError(f"no endmodule found for module '{module_name}'")
    return text[start : start + em.end()].rstrip() + "\n"


@dataclass
class DesignDirs:
    """Paths of the generated work copy of one design."""
    root: Path
    src: Path
    tb_templates: Path
    config: Path
    golden_dir: Path
    golden_file: Path
    shifted_golden: Path
    primary_rtl: Path
    rtl_basenames: list[str]


def build_design_dir(
    d: Design,
    dest_root: Path,
    n_vectors: int = N_VECTORS_DEFAULT,
    sec_golden_file: str | None = None,
) -> DesignDirs:
    """Copy the sources and write the testbenches and config.sh of one design."""
    if n_vectors < 2:
        raise ValueError("at least two verification vectors are required")
    dest_root = dest_root.resolve()
    if any(Path(source).resolve().is_relative_to(dest_root) for source in d.src_files):
        raise ValueError("source files must be outside the generated design directory")
    if dest_root.exists():
        shutil.rmtree(dest_root)
    src = dest_root / "src"
    tb = dest_root / "tb" / "templates"
    golden_dir = dest_root / "golden"
    for p in (src, tb, golden_dir):
        p.mkdir(parents=True)
    if d.extract_top:
        block = extract_module(Path(d.src_files[0]).read_text(), d.top)
        primary_name = f"{d.top}.{d.lang}"
        (src / primary_name).write_text(block)
        supporting = [Path(source) for source in d.src_files[1:]]
        if primary_name in {source.name for source in supporting}:
            raise ValueError(
                "an extracted top filename conflicts with a supporting source"
            )
        for source in supporting:
            shutil.copy2(source, src / source.name)
        rtl_basenames = [primary_name] + [source.name for source in supporting]
    else:
        for f in d.src_files:
            shutil.copy2(f, src / Path(f).name)
        rtl_basenames = [Path(f).name for f in d.src_files]
    golden_file = (golden_dir / "golden_default.txt").resolve()
    shifted = (golden_dir / "golden_default_shifted.txt").resolve()
    (tb / f"tb_gold_{d.key}.sv").write_text(
        gen_tb_gold(d, str(golden_file)).replace("{N}", str(n_vectors))
    )
    (tb / f"tb_verify_{d.key}.sv").write_text(
        gen_tb_verify(d, str(golden_file)).replace("{N}", str(n_vectors))
    )
    (tb / f"tb_negctrl_{d.key}.sv").write_text(
        gen_tb_negctrl(d, str(shifted)).replace("{N}", str(n_vectors))
    )
    (tb / f"tb_pt_{d.key}.sv").write_text(gen_tb_pt(d))
    (dest_root / "config.sh").write_text(
        gen_config_sh(d, rtl_basenames, sec_golden_file=sec_golden_file)
    )
    return DesignDirs(
        root=dest_root,
        src=src,
        tb_templates=tb,
        config=dest_root / "config.sh",
        golden_dir=golden_dir,
        golden_file=golden_file,
        shifted_golden=shifted,
        primary_rtl=src / rtl_basenames[0],
        rtl_basenames=rtl_basenames,
    )


# ---------------------------------------------------------------------------
# RTL simulation: golden trace and negative control
# ---------------------------------------------------------------------------
def _run_questa_rtl(
    rtl_files: list[Path],
    tb_file: Path,
    tb_module: str,
    workdir: Path,
    log_fn,
) -> tuple[bool, str]:
    """Run an RTL testbench without shell interpolation."""
    workdir = workdir.resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    work_lib = workdir / "work"
    commands = [
        [os.environ.get("RTL_FLOW_VLIB", "vlib"), str(work_lib)],
        [
            os.environ.get("RTL_FLOW_VLOG", "vlog"),
            "-sv",
            "-work",
            str(work_lib),
            *(str(p.resolve()) for p in rtl_files),
            str(tb_file.resolve()),
        ],
        [
            os.environ.get("RTL_FLOW_VSIM", "vsim"),
            "-c",
            "-lib",
            str(work_lib),
            tb_module,
            "-do",
            "run -all; quit -f",
        ],
    ]
    logs = []
    ok = True
    try:
        for command in commands:
            proc = subprocess.run(command, cwd=workdir, capture_output=True, text=True)
            logs.append((proc.stdout or "") + (proc.stderr or ""))
            if proc.returncode:
                ok = False
                break
    except OSError as exc:
        logs.append(str(exc))
        ok = False
    log = "\n".join(logs)
    (workdir / f"{tb_module}.log").write_text(log)
    shutil.rmtree(work_lib, ignore_errors=True)
    for junk in ("transcript", "vsim.wlf", "vsim_stacktrace.vstf"):
        (workdir / junk).unlink(missing_ok=True)
    if not ok:
        log_fn(
            f'[simulation] {tb_module}: tool error; see {workdir / (tb_module + ".log")}'
        )
    return ok, log


def generate_golden(d: Design, dirs: DesignDirs, log_fn) -> bool:
    """Simulate the baseline RTL, write the golden trace, and write its shifted copy."""
    rtl = [dirs.src / b for b in dirs.rtl_basenames]
    tb = dirs.tb_templates / f"tb_gold_{d.key}.sv"
    (ok, log) = _run_questa_rtl(
        rtl, tb, f"tb_gold_{d.key}", dirs.root / "_golden_gen", log_fn
    )
    if not ok or "GOLDEN_DONE" not in log or (not dirs.golden_file.is_file()):
        log_fn(
            f"[golden] FAILED to generate golden for {d.key} (GOLDEN_DONE={'GOLDEN_DONE' in log}, file={dirs.golden_file.is_file()})"
        )
        return False
    lines = dirs.golden_file.read_text().splitlines()
    done = re.search(r"GOLDEN_DONE\s+(\d+)", log)
    if not done or len(lines) != int(done.group(1)):
        log_fn(f"[golden] {d.key}: generated trace is incomplete")
        return False
    if len(lines) < 2:
        log_fn(f"[golden] {d.key}: golden has <2 lines — cannot build negative control")
        return False
    shifted = lines[1:] + lines[:1]
    dirs.shifted_golden.write_text("\n".join(shifted) + "\n")
    distinct = len(set(lines))
    log_fn(
        f"[golden] {d.key}: {len(lines)} cycles, {distinct} distinct output lines"
        + ("  [WARN: outputs ~constant, check may be weak]" if distinct < 3 else "")
    )
    return True


@dataclass
class SynthOutcome:
    """Result of one flow run: success, the failed step, and the CSV and work paths."""
    ok: bool
    failed_step: str
    csv_path: str
    run_id: str
    work_dir: Path | None


def negative_control_passes_as_fail(d: Design, dirs: DesignDirs, log_fn) -> bool:
    """Return True when the baseline fails against the shifted golden trace, as it must."""
    rtl = [dirs.src / b for b in dirs.rtl_basenames]
    tb = dirs.tb_templates / f"tb_negctrl_{d.key}.sv"
    (ok, log) = _run_questa_rtl(
        rtl, tb, f"tb_negctrl_{d.key}", dirs.root / "_negctrl", log_fn
    )
    passed_as_fail = (
        ok
        and "FAILURES DETECTED" in log
        and "MISMATCH cyc=" in log
        and "ALL TESTS PASSED" not in log
    )
    if not passed_as_fail:
        log_fn(
            f"[negctrl] {d.key}: VACUOUS-CHECK WARNING — v0 matched a shifted golden (the differential check may not be exercising outputs)."
        )
    return passed_as_fail


# ---------------------------------------------------------------------------
# Flow runs with license and disk handling
# ---------------------------------------------------------------------------
# A step that fails for lack of a tool license (or a full disk) is not a design
# failure: the flow waits and runs the same step again.
LICENSE_FAIL_RE = re.compile(
    r"SEC-50|DCSH-1|licenses are in use|Unable to obtain feature"
    r"|Licensed number of users already reached|Flex(?:NET|Net|net) Licensing error"
    r"|(?:unable to|could not|cannot|failed to) (?:check ?out|obtain|get) "
    r"(?:a |the |any )?(?:\w+ )?licen[cs]e"
    r"|licen[cs]e (?:request|checkout|check-out|check out) (?:failed|error|denied)",
    re.IGNORECASE,
)
LICENSE_WAIT_S = int(os.environ.get("RTL_FLOW_LICENSE_WAIT_S", "300"))
LICENSE_MAX_WAIT_S = int(os.environ.get("RTL_FLOW_LICENSE_MAX_WAIT_S", str(12 * 3600)))
_LICENSE_LOG_DIRS = (
    "syn/logs",
    "verify_rtl/logs",
    "verify_syn/logs",
    "sec_rtl/logs",
    "sim-syn/logs",
    "pt/terminal_logs",
    "pt/reports",
)
LOG_FN = None
LICENSE_WAITS: list = []


class LicenseUnavailable(RuntimeError):
    """A tool license or the disk stayed unavailable for the maximum waiting time."""
    pass


def license_failure(work_dir: Path | None) -> str | None:
    """Return the log line of a license failure in a work directory, or None."""
    if not work_dir or not work_dir.is_dir():
        return None
    files = [
        work_dir / n for n in ("progress.txt", "status.txt", "verify_failures.txt")
    ]
    for sub in _LICENSE_LOG_DIRS:
        d = work_dir / sub
        if d.is_dir():
            files += sorted(p for p in d.rglob("*") if p.is_file())
    for f in files:
        if not f.is_file():
            continue
        try:
            with open(f, errors="replace") as fh:
                text = fh.read(4000000)
        except OSError:
            continue
        m = LICENSE_FAIL_RE.search(text)
        if m:
            a = text.rfind("\n", 0, m.start()) + 1
            b = text.find("\n", m.end())
            line = text[a : b if b != -1 else len(text)].strip()
            return f"{f.relative_to(work_dir)}: {line[:200]}"
    return None


def disk_full(probe_dir: Path) -> str | None:
    """Return a message when the disk or the quota is full at probe_dir, or None."""
    import errno

    p = Path(probe_dir) / f".disk_probe_{os.getpid()}"
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, b"\x00" * 262144)
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as e:
        if e.errno in (errno.EDQUOT, errno.ENOSPC):
            return f"disk quota or disk space full ({e.strerror}) at {probe_dir}"
        return None
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    return None


def take_license_waits() -> list:
    """Return and clear the license waits recorded since the last call."""
    out = [
        {"at": t, "step": s, "seconds": w, "reason": r}
        for (t, s, w, r) in LICENSE_WAITS
    ]
    LICENSE_WAITS.clear()
    return out


def run_flow(
    dirs: DesignDirs,
    out_dir: Path,
    *,
    skip_verify: bool,
    skip_verify_syn: bool,
    keep_work: bool = True,
    use_sec: bool = False,
    verify_only: bool = False,
) -> SynthOutcome:
    """Run the flow; wait and run it again while a license or the disk is unavailable."""
    waited = 0
    while True:
        out = run_flow_once(
            dirs,
            out_dir,
            skip_verify=skip_verify,
            skip_verify_syn=skip_verify_syn,
            keep_work=keep_work,
            use_sec=use_sec,
            verify_only=verify_only,
        )
        if out.ok:
            return out
        why = license_failure(out.work_dir) or disk_full(out_dir)
        if not why:
            return out
        if waited >= LICENSE_MAX_WAIT_S:
            raise LicenseUnavailable(
                f"step '{out.failed_step}' could not run (no license or full disk) for {waited / 3600:.1f} h ({why}); the run stops and scores nothing"
            )
        delay = min(max(1, LICENSE_WAIT_S), LICENSE_MAX_WAIT_S - waited)
        msg = f"[license] step '{out.failed_step}' could not run ({why}); wait {delay} s and retry (waited {waited} s, limit {LICENSE_MAX_WAIT_S} s)"
        (LOG_FN or print)(msg)
        LICENSE_WAITS.append((time.strftime("%F %T"), out.failed_step, delay, why))
        drop_work_dir(out.work_dir)
        time.sleep(delay)
        waited += delay


def run_flow_once(
    dirs: DesignDirs,
    out_dir: Path,
    *,
    skip_verify: bool,
    skip_verify_syn: bool,
    keep_work: bool = True,
    use_sec: bool = False,
    verify_only: bool = False,
) -> SynthOutcome:
    """Run the flow once with the timing framework or, with use_sec, the formal framework."""
    out_dir.mkdir(parents=True, exist_ok=True)
    prev_fw = synthesis.CLEAN_FRAMEWORK
    synthesis.CLEAN_FRAMEWORK = CLEAN_FRAMEWORK_SEC if use_sec else CLEAN_FRAMEWORK_TIMING
    try:
        res = synthesis.run_synthesis(
            src_dir=str(dirs.src),
            tb_dir=str(dirs.tb_templates),
            config_file=str(dirs.config),
            output_dir=str(out_dir),
            skip_verify=skip_verify,
            skip_verify_syn=skip_verify_syn,
            verify_only=verify_only,
            cleanup_on_success=not keep_work,
        )
    finally:
        synthesis.CLEAN_FRAMEWORK = prev_fw
    work = Path(f"{synthesis.WORK_BASE}_{res.run_id}") if res.run_id else None
    return SynthOutcome(
        ok=res.return_code == 0,
        failed_step=res.failed_step,
        csv_path=res.csv_path,
        run_id=res.run_id,
        work_dir=work if work and work.is_dir() else None,
    )


# ---------------------------------------------------------------------------
# Log harvesting and cleanup
# ---------------------------------------------------------------------------
_SIM_JUNK_SUFFIXES = (".qdb", ".qtl", ".qpg", ".wlf", ".vstf")
_SIM_JUNK_NAMES = ("transcript", "vsim_stacktrace.vstf")


def _harvest_ignore(src, names):
    """Select simulator libraries and temporary files that log harvesting skips."""
    drop = set()
    for n in names:
        p = Path(src) / n
        if p.is_dir():
            if (p / "_info").exists() or n.startswith("alib-5"):
                drop.add(n)
        elif n in _SIM_JUNK_NAMES or n.endswith(_SIM_JUNK_SUFFIXES):
            drop.add(n)
    return drop


_WORK_LIB_MARKERS = ("_info", "_lib.qdb", "_vmake")


def _is_tool_work_lib(d: Path) -> bool:
    """Return True for a simulator or synthesis library directory."""
    if d.name.startswith("alib-"):
        return True
    return any((d / m).exists() for m in _WORK_LIB_MARKERS)


def purge_work_libs(root: Path | None) -> int:
    """Delete simulator and synthesis library directories under root."""
    if not root or not root.is_dir():
        return 0
    removed = 0
    victims = [
        d
        for d in root.rglob("*")
        if d.is_dir() and (not d.is_symlink()) and _is_tool_work_lib(d)
    ]
    for d in sorted(victims, key=lambda p: len(p.parts), reverse=True):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def harvest_logs(work_dir: Path | None, dest: Path) -> None:
    """Copy the logs, reports, netlist, and results of a work directory to dest."""
    if not work_dir or not work_dir.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for sub in (
        "syn/logs",
        "syn/reports",
        "syn/outputs",
        "verify_rtl/logs",
        "verify_syn/logs",
        "sec_rtl/logs",
        "sim-syn/logs",
        "pt/reports",
        "pt/terminal_logs",
        "results",
    ):
        s = work_dir / sub
        if s.is_dir():
            d = dest / sub
            d.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copytree(s, d, dirs_exist_ok=True, ignore=_harvest_ignore)
            except Exception:
                pass
    purge_work_libs(dest)


def drop_work_dir(work_dir: Path | None) -> None:
    """Delete a work directory."""
    if work_dir and work_dir.is_dir():
        shutil.rmtree(work_dir, ignore_errors=True)
