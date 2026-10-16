# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Exercise the flow with fake tools; no EDA executable or license is used."""

from __future__ import annotations

import csv
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

PUBLIC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PUBLIC_ROOT))

import harness  # noqa: E402
import synthesis  # noqa: E402
from designs import Design  # noqa: E402


FAKE_TOOL = r"""
from pathlib import Path
import os
import re
import sys

kind = Path(sys.argv[0]).stem
args = sys.argv[1:]
if kind == "vlib":
    Path(args[0]).mkdir(parents=True, exist_ok=True)
elif kind == "vlog":
    print(" ".join(args))
    if os.environ.get("MOCK_COMPILE_FAIL"):
        print("** Error: compile failed")
        sys.exit(1)
elif kind == "vsim":
    if any("tb_pt_" in arg for arg in args):
        template = next(Path.cwd().glob("rtl/tb/debug/sim-syn/run_001/*.sv"))
        text = template.read_text()
        path = Path(re.search(r'\$dumpfile\("([^"\n]+)"\)', text).group(1))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("$date mock $end\n$timescale 1ns $end\n#0\n")
        print("POWER_DONE")
    elif os.environ.get("MOCK_VERIFY_FAIL"):
        print("MISMATCH cyc=1 result dut=0 exp=1")
        print("FAILURES DETECTED (1 mismatches)")
    else:
        print("ALL TESTS PASSED")
elif kind == "dc_shell":
    text = Path(args[args.index("-f") + 1]).read_text()
    reports = [
        ("report_area", "Total cell area: 12.5\n"),
        ("report_qor", "Critical Path Length: 0.2\nCritical Path Length: 0.25\n"),
    ]
    for command, body in reports:
        path = Path(re.search(command + r'.*? > "([^"]+)"', text).group(1))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    path = Path(re.search(r'write .*?-output "([^"]+)"', text).group(1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("module core(); endmodule\n")
    print("Synthesis complete")
elif kind == "lc_shell":
    text = Path(args[args.index("-f") + 1]).read_text()
    path = Path(re.search(r'-output "([^"]+)"', text).group(1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("mock compiled library")
    print("Compilation complete")
elif kind == "pt_shell":
    text = Path(args[args.index("-f") + 1]).read_text()
    for value in re.findall(r'redirect -file "([^"]+)"', text):
        path = Path(value)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Power Units = 1 mW\nTotal 0.3 0.5 0.2 1.0 (100.0%)\n")
    print("Power analysis complete")
elif kind == "jaspergold":
    print("undetermined" if os.environ.get("MOCK_UNPROVEN") else "proven")
else:
    sys.exit("Unknown fake tool: " + kind)
"""


class SynthesisFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ares-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        env = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("RTL_FLOW_", "MOCK_"))
        }
        env["RTL_FLOW_PYTHON"] = sys.executable
        for tool in (
            "vlib",
            "vlog",
            "vsim",
            "dc_shell",
            "lc_shell",
            "pt_shell",
            "jaspergold",
        ):
            path = self.root / tool
            path.write_text(f"#!{sys.executable}\n" + FAKE_TOOL)
            path.chmod(0o755)
            env["RTL_FLOW_" + tool.upper()] = str(path)
        technology = self.root / "technology with spaces"
        technology.mkdir()
        self.library = technology / "std.lib"
        self.library.write_text("mock technology input")
        self.model = technology / "cells.v"
        self.model.write_text("module mock_cell(); endmodule\n")
        env.update(
            {
                "RTL_FLOW_TECH_DIR": str(technology),
                "RTL_FLOW_SYNTH_LIBS": self.library.name,
                "RTL_FLOW_SIM_MODELS": self.model.name,
                "RTL_FLOW_TECH_CACHE": str(self.root / "technology cache"),
            }
        )
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.work = mock.patch.object(
            synthesis, "WORK_BASE", str(self.root / "work run")
        )
        self.work.start()
        self.addCleanup(self.work.stop)
        self.framework = mock.patch.object(
            synthesis, "CLEAN_FRAMEWORK", harness.CLEAN_FRAMEWORK_TIMING
        )
        self.framework.start()
        self.addCleanup(self.framework.stop)
        self.source = self.root / "input with spaces.sv"
        self.source.write_text(
            "module core(input logic clk, rst, data, output logic result); endmodule\n"
        )
        self.design = Design(
            key="case",
            top="core",
            src_files=[str(self.source)],
            clocks=["clk"],
            resets=[("rst", "high")],
            inputs=[("data", 1)],
            outputs=[("result", 1)],
        )
        self.dirs = harness.build_design_dir(
            self.design, self.root / "prepared design", n_vectors=4
        )

    def run_flow(self, **options):
        defaults = dict(skip_verify=False, skip_verify_syn=False)
        defaults.update(options)
        with redirect_stdout(io.StringIO()):
            return harness.run_flow_once(
                self.dirs, self.root / "outputs", **defaults
            )

    def test_complete_flow_records_units_period_and_all_io_constraints(self):
        outcome = self.run_flow()
        self.assertTrue(outcome.ok, outcome)
        with Path(outcome.csv_path).open() as csv_file:
            row = list(csv.DictReader(csv_file))[0]
        self.assertEqual(float(row["area"]), 12.5)
        self.assertEqual(float(row["timing_ns"]), 0.25)
        self.assertEqual(float(row["power_default"]), 0.001)
        self.assertAlmostEqual(float(row["simulation_period_ns"]), 0.25 * 1.10)
        proof = outcome.work_dir / "syn/logs/run_001/synth_filled.tcl"
        for command in ("create_clock", "set_input_delay", "set_output_delay"):
            self.assertIn(command, proof.read_text())
        self.assertIn("input with spaces.sv", proof.read_text())
        self.assertEqual(set(self.library.parent.iterdir()), {self.library, self.model})
        self.assertEqual(len(list((self.root / "technology cache").glob("*.db"))), 1)
        compile_log = (
            outcome.work_dir
            / "verify_syn/logs/run_001/compile_tb_verify_case_default.log"
        )
        self.assertIn("core_mapped.v", compile_log.read_text())
        self.assertIn("cells.v", compile_log.read_text())
        self.assertNotIn("input with spaces.sv", compile_log.read_text())

    def test_resume_reuses_existing_synthesis_and_switching_activity(self):
        outcome = self.run_flow()
        self.assertTrue(outcome.ok)
        netlist = outcome.work_dir / "syn/outputs/run_001/core_mapped.v"
        timestamp = netlist.stat().st_mtime_ns
        with redirect_stdout(io.StringIO()):
            resumed = synthesis.run_synthesis(
                "",
                "",
                "",
                output_dir=str(self.root / "resumed"),
                resume_run_id=outcome.run_id,
                start_step="pt",
                cleanup_on_success=False,
            )
        self.assertEqual(resumed.return_code, 0)
        self.assertEqual(netlist.stat().st_mtime_ns, timestamp)

    def test_verification_failure_stops_before_synthesis(self):
        os.environ["MOCK_VERIFY_FAIL"] = "1"
        outcome = self.run_flow()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_step, "verify")
        self.assertFalse((outcome.work_dir / "syn/outputs").exists())
        self.assertIn(
            "functional verification failed",
            (outcome.work_dir / "verify_failures.txt").read_text(),
        )

    def test_compile_failure_stops_before_simulation(self):
        os.environ["MOCK_COMPILE_FAIL"] = "1"
        outcome = self.run_flow()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failed_step, "verify")
        self.assertFalse(
            list((outcome.work_dir / "verify_rtl/logs").rglob("verify_rtl_*.log"))
        )

    def test_formal_only_requires_a_proven_result(self):
        golden = self.dirs.golden_dir / "reference.sv"
        shutil.copy2(self.source, golden)
        self.dirs.config.write_text(
            harness.gen_config_sh(self.design, self.dirs.rtl_basenames, str(golden))
        )
        outcome = self.run_flow(use_sec=True, verify_only=True, skip_verify_syn=True)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.csv_path, "")
        self.assertFalse((outcome.work_dir / "syn/outputs").exists())
        self.assertIn(
            "check_sec -analyze -spec",
            (outcome.work_dir / "sec_rtl/logs/sec_core.tcl").read_text(),
        )
        os.environ["MOCK_UNPROVEN"] = "1"
        unproven = self.run_flow(use_sec=True, verify_only=True, skip_verify_syn=True)
        self.assertFalse(unproven.ok)
        self.assertEqual(unproven.failed_step, "verify")

    def test_transfer_only_creates_a_portable_work_directory(self):
        with redirect_stdout(io.StringIO()):
            result = synthesis.run_synthesis(
                str(self.dirs.src),
                str(self.dirs.tb_templates),
                str(self.dirs.config),
                run_synthesis_script=False,
            )
        self.assertEqual(result.return_code, 0)
        work = Path(f"{synthesis.WORK_BASE}_{result.run_id}")
        self.assertTrue((work / "runner.py").is_file())
        self.assertFalse((work / "status.txt").exists())
        self.assertFalse(any(path.is_symlink() for path in work.rglob("*")))
        with redirect_stdout(io.StringIO()):
            invalid = synthesis.run_synthesis("", "", "", resume_run_id="../escape")
        self.assertNotEqual(invalid.return_code, 0)

    def test_scratch_width_adapts_to_the_interface(self):
        wide = Design(
            key="wide",
            top="core",
            src_files=[str(self.source)],
            clocks=[],
            resets=[],
            inputs=[("data", 137)],
            outputs=[("result", 211)],
        )
        self.assertIn("logic [210:0] _rnd", harness.gen_tb_gold(wide, "golden.txt"))
        self.assertIn("[210:0] dut", harness.gen_tb_verify(wide, "golden.txt"))

    def test_extracting_the_top_keeps_supporting_sources(self):
        bundled = self.root / "bundled.sv"
        bundled.write_text("module core(); endmodule\nmodule unused(); endmodule\n")
        support = self.root / "support.sv"
        support.write_text("module support(); endmodule\n")
        design = Design(
            key="extracted",
            top="core",
            src_files=[str(bundled), str(support)],
            clocks=[],
            resets=[],
            inputs=[],
            outputs=[("result", 1)],
            lang="sv",
            extract_top=True,
        )
        dirs = harness.build_design_dir(design, self.root / "extracted", n_vectors=2)
        self.assertEqual(dirs.rtl_basenames, ["core.sv", "support.sv"])
        self.assertNotIn("module unused", dirs.primary_rtl.read_text())
        self.assertEqual((dirs.src / support.name).read_text(), support.read_text())

    def test_module_extraction_ignores_comment_and_string_keywords(self):
        text = '// module core(); endmodule\nmodule core(); string note="endmodule"; /* endmodule */ endmodule\nmodule extra(); endmodule\n'
        module = harness.extract_module(text, "core")
        self.assertTrue(module.startswith("module core();"))
        self.assertIn('string note="endmodule"', module)
        self.assertNotIn("module extra", module)

    def test_negative_control_requires_a_real_output_mismatch(self):
        with mock.patch.object(
            harness, "_run_questa_rtl", return_value=(True, "TIMEOUT")
        ):
            self.assertFalse(
                harness.negative_control_passes_as_fail(
                    self.design, self.dirs, lambda message: None
                )
            )
        with mock.patch.object(
            harness,
            "_run_questa_rtl",
            return_value=(True, "MISMATCH cyc=1\nFAILURES DETECTED (1 mismatches)"),
        ):
            self.assertTrue(
                harness.negative_control_passes_as_fail(
                    self.design, self.dirs, lambda message: None
                )
            )

    def test_license_detection_reads_tool_logs(self):
        work = self.root / "license logs"
        log = work / "syn/logs/run_001/syn.log"
        log.parent.mkdir(parents=True)
        log.write_text("All licenses are in use. (SEC-50)\n")
        self.assertIn("SEC-50", harness.license_failure(work))
        log.write_text("ordinary compile error\n")
        self.assertIsNone(harness.license_failure(work))

    def test_license_retry_does_not_score_an_infrastructure_failure(self):
        failure = harness.SynthOutcome(False, "syn", "", "failed", None)
        success = harness.SynthOutcome(True, "", "result.csv", "success", None)
        with mock.patch.object(
            harness, "run_flow_once", side_effect=[failure, success]
        ) as call, mock.patch.object(
            harness, "license_failure", return_value="license unavailable"
        ), mock.patch.object(
            harness, "LICENSE_WAIT_S", 2
        ), mock.patch.object(
            harness, "LICENSE_MAX_WAIT_S", 5
        ), mock.patch.object(
            harness.time, "sleep"
        ) as sleep, mock.patch.object(
            harness, "LOG_FN", lambda message: None
        ):
            result = harness.run_flow(
                self.dirs,
                self.root / "outputs",
                skip_verify=False,
                skip_verify_syn=False,
            )
        self.assertIs(result, success)
        self.assertEqual(call.call_count, 2)
        sleep.assert_called_once_with(2)
        waits = harness.take_license_waits()
        self.assertEqual(waits[-1]["seconds"], 2)


    def test_compiled_library_is_cached_between_runs(self):
        first = self.run_flow()
        second = self.run_flow()
        self.assertTrue(first.ok and second.ok)
        self.assertTrue(list((first.work_dir / "syn/logs/run_001").glob("lc_*.log")))
        self.assertFalse(list((second.work_dir / "syn/logs/run_001").glob("lc_*.log")))
        self.assertEqual(len(list((self.root / "technology cache").glob("*.db"))), 1)

    def test_bundled_nangate45_is_the_default_technology(self):
        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            env = synthesis.flow_environment()
        technology = Path(env["RTL_FLOW_TECH_DIR"])
        self.assertTrue((technology / env["RTL_FLOW_SYNTH_LIBS"]).is_file())
        self.assertTrue((technology / env["RTL_FLOW_SIM_MODELS"]).is_file())
        # A configured library replaces the whole default.
        with mock.patch.dict(os.environ, {"RTL_FLOW_SYNTH_LIBS": "/abs/own.lib"}, clear=True):
            env = synthesis.flow_environment()
        self.assertNotIn("RTL_FLOW_TECH_DIR", env)
        self.assertEqual(env["RTL_FLOW_SYNTH_LIBS"], "/abs/own.lib")

if __name__ == "__main__":
    unittest.main()
