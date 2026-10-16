# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Tests of the optimization loop with synthetic measurements and model replies."""

import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import optimize
import harness
from designs import Design, load_designs

# The adaptive policy of the paper with patience 1, so that a short test shows a burst.
FAST_ADAPTIVE = dict(patience=1, fail_weight=1, reset_rho=0, high_iters=1)
# Module settings that main() changes.
MAIN_SETTINGS = (
    "PROVIDER", "MODEL", "EFFORT", "MAX_OUTPUT_TOKENS", "CODEX_HOME", "CODEX_BIN",
    "LEAN", "VECTORS", "_NOTES",
    "_NOTES_META", "_RUN_SETTINGS", "_REGISTRY_SHA256",
)


class OptimizationTests(unittest.TestCase):
    def test_registry_resolves_relative_sources_and_rejects_duplicate_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "unit.sv").write_text("module unit; endmodule\n")
            entry = dict(
                key="unit",
                top="unit",
                src_files=["unit.sv"],
                clocks=["clk"],
                resets=[["rst", "low"]],
                inputs=[["data", 8]],
                outputs=[["result", 8]],
                lang="sv",
            )
            config = root / "designs.json"
            config.write_text(json.dumps({"designs": [entry]}))
            self.assertEqual(
                load_designs(config)["unit"].src_files, [str(root / "unit.sv")]
            )
            entry["inputs"].append(["clk", 1])
            config.write_text(json.dumps({"designs": [entry]}))
            with self.assertRaisesRegex(ValueError, "more than one role"):
                load_designs(config)

    def test_example_registry_loads(self):
        registry = load_designs(ROOT / "examples" / "designs.json")
        design = registry["sad4"]
        self.assertEqual(design.top, "sad4")
        self.assertTrue(Path(design.src_files[0]).is_file())
        self.assertFalse(design.combinational)

    def test_codex_accounts_empty_paid_replies_before_retry(self):
        def record(text):
            return dict(
                text=text,
                thread_id="session",
                turn_failed="temporary error",
                errors=[],
                stderr="",
                tokens=dict(
                    in_tok=1_000_000, cached_in_tok=0, out_tok=0, reasoning_tok=0
                ),
            )

        replies = iter([record(""), record("valid reply")])
        backend = types.SimpleNamespace(
            CodexError=RuntimeError, call_codex=lambda *a, **k: next(replies)
        )
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(optimize, "_codex_module", return_value=backend)
            )
            stack.enter_context(
                patch.object(
                    optimize, "_CODEX_PRICE", {"in": 1e-6, "cached": 0, "out": 0}
                )
            )
            stack.enter_context(
                patch.object(
                    optimize, "_CODEX_PRICE_LONG", {"in": 1e-6, "cached": 0, "out": 0}
                )
            )
            stack.enter_context(patch.object(optimize, "_CALL_COST_CAP", None))
            stack.enter_context(patch.object(optimize.time, "sleep"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            totals = optimize._new_totals()
            text, _, usage = optimize.call_codex_provider(
                "prompt", Path(tmp), "tag", totals, None
            )
            self.assertEqual(text, "valid reply")
            self.assertEqual(totals["cost"], 2)
            self.assertEqual(usage["calls"], 2)
            self.assertEqual(usage["cost"], 2)

    def test_paper_price_file_matches_the_recorded_prices(self):
        with contextlib.ExitStack() as stack:
            for name in ("_CODEX_PRICE", "_CODEX_PRICE_LONG", "_CODEX_LONG_CTX"):
                stack.enter_context(patch.object(optimize, name, None))
            optimize._load_prices(ROOT / "config" / "prices.gpt-5.5.json")
            # USD 5, 0.50, and 30 per million input, cached-input, and output tokens.
            self.assertAlmostEqual(optimize._codex_cost(100_000, 0, 0), 0.5)
            self.assertAlmostEqual(optimize._codex_cost(100_000, 100_000, 0), 0.05)
            self.assertAlmostEqual(optimize._codex_cost(0, 0, 100_000), 3.0)
            # Above 272,000 input tokens the long-context prices apply (USD 10 per million).
            self.assertAlmostEqual(optimize._codex_cost(300_000, 0, 0), 3.0)

    def test_invalid_measurements_cannot_improve_score(self):
        base = dict(area=10.0, mean_power=1.0, timing_ns=2.0)
        for invalid in (None, float("nan"), float("inf"), -1):
            self.assertEqual(
                optimize.ppa_fom(dict(base, timing_ns=invalid), base), float("inf")
            )
        self.assertAlmostEqual(optimize.ppa_fom(dict(base, area=5), base), 0.5)
        with self.assertRaises(ValueError):
            optimize.ppa_fom(base, dict(base, mean_power=0))

    def run_mocked(
        self,
        *,
        failures=(),
        max_cost=None,
        adaptive=None,
        start_high=False,
        malformed_first=False,
        n_iters=3,
        areas=None,
        observations=None,
        notes="",
        compacted_calls=(),
    ):
        """Run the loop with a fake flow and a fake model; return the result and records."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "unit.sv"
            source.write_text("module unit; // baseline\nendmodule\n")
            design = Design(
                "unit",
                "unit",
                [str(source)],
                ["clk"],
                [("rst", "low")],
                [("data", 8)],
                [("result", 8)],
                lang="sv",
            )
            measurement_count = 0
            call_count = 0
            efforts = []

            def synth(dirs, out_dir, **kwargs):
                nonlocal measurement_count
                measurement_count += 1
                out_dir.mkdir(parents=True, exist_ok=True)
                csv = out_dir / "metrics.csv"
                # The first candidate has a smaller area; later ones are worse.
                area = (
                    areas[measurement_count - 1]
                    if areas and measurement_count <= len(areas)
                    else (
                        10
                        if measurement_count == 1
                        else 5 if measurement_count == 2 else 20
                    )
                )
                csv.write_text(f"area,timing_ns,power_default\n{area},2,1\n")
                failed = measurement_count in failures
                return harness.SynthOutcome(
                    not failed, "verify" if failed else "", str(csv), "mock", None
                )

            def model(prompt, run_dir, tag, totals, resume, effort=None, **kwargs):
                nonlocal call_count
                if observations is not None:
                    observations.append(
                        {
                            "source": (
                                root / "out" / "designs" / "unit" / "src" / "unit.sv"
                            ).read_text(),
                            "resume": resume,
                            "prompt": prompt,
                            "effort": effort,
                        }
                    )
                call_count += 1
                efforts.append(effort)
                usage = optimize._new_totals()
                usage.update(cost=1.0, calls=1, compacted=call_count in compacted_calls)
                totals["cost"] += 1
                totals["calls"] += 1
                text = (
                    "no fenced source"
                    if malformed_first and call_count == 1
                    else f"```code\nmodule unit; // candidate {call_count}\nendmodule\n```"
                )
                return text, resume or f"mock-session-{call_count}", usage

            def golden(d, dirs, log):
                dirs.golden_file.write_text("0\n1\n2\n")
                return True

            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(harness, "run_flow", side_effect=synth))
                stack.enter_context(
                    patch.object(harness, "generate_golden", side_effect=golden)
                )
                stack.enter_context(
                    patch.object(
                        harness, "negative_control_passes_as_fail", return_value=True
                    )
                )
                stack.enter_context(
                    patch.object(
                        optimize,
                        "_flow_proof",
                        return_value={"ok": True, "summary": "mock", "constraints": []},
                    )
                )
                stack.enter_context(
                    patch.object(optimize, "call_model", side_effect=model)
                )
                stack.enter_context(patch.object(optimize, "EFFORT", "medium"))
                stack.enter_context(patch.object(optimize, "_NOTES", notes))
                stack.enter_context(patch.object(optimize, "_notes_check"))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                result = optimize.run_one_design(
                    design,
                    n_iters,
                    8,
                    root / "out",
                    max_cost=max_cost,
                    adaptive=adaptive,
                    start_high=start_high,
                )
            records = [
                json.loads(line)
                for line in Path(result["test_data"]).read_text().splitlines()
            ]
            return result, records, call_count, efforts

    def test_valid_better_candidate_is_kept_and_worse_candidate_reverted(self):
        result, records, calls, _ = self.run_mocked()
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["deliverable_fom"], 0.5)
        self.assertEqual(calls, 3)
        self.assertEqual(
            [r["accepted"] for r in records if r["event"] == "iteration"],
            [True, False, False],
        )

    def test_failed_verification_cannot_become_best(self):
        result, records, _, _ = self.run_mocked(failures=(2,))
        self.assertEqual(result["deliverable_fom"], 1.0)
        self.assertIn(
            "rtl-verify-fail",
            next(r["outcome"] for r in records if r["event"] == "iteration"),
        )

    def test_budget_prevents_extra_formatting_request(self):
        result, _, calls, _ = self.run_mocked(max_cost=1.0, malformed_first=True)
        self.assertEqual(calls, 1)
        self.assertEqual(result["accepted"], 0)

    def test_budget_stops_before_the_next_iteration(self):
        result, records, calls, _ = self.run_mocked(max_cost=2.0, n_iters=5)
        self.assertEqual(calls, 2)
        stop = next(r for r in records if r["event"] == "cost_budget_stop")
        self.assertEqual(stop["iter"], 3)

    def test_adaptive_cost_includes_formatting_retry(self):
        _, rows, calls, efforts = self.run_mocked(
            adaptive=FAST_ADAPTIVE, malformed_first=True
        )
        iterations = [row for row in rows if row["event"] == "iteration"]
        self.assertEqual(calls, 4)
        self.assertEqual(iterations[0]["cumulative_totals"]["cost"], 2.0)
        self.assertEqual(efforts[-1], "high")
        self.assertEqual(iterations[-1]["adaptive_high_cost_frac"], 0.25)

    def test_fixed_effort_always_starts_from_best_source(self):
        seen = []
        result, rows, calls, _ = self.run_mocked(observations=seen, n_iters=4)
        best_source = next(row["code"] for row in rows if row.get("accepted"))
        self.assertEqual(calls, 4)
        self.assertIn("baseline", seen[0]["source"])
        self.assertEqual([row["source"] for row in seen[1:]], [best_source] * 3)
        self.assertEqual(result["deliverable_fom"], 0.5)

    def test_adaptive_effort_always_starts_from_best_source(self):
        seen = []
        result, rows, calls, efforts = self.run_mocked(
            adaptive=dict(FAST_ADAPTIVE, high_iters=2),
            observations=seen,
            n_iters=4,
        )
        best_source = next(row["code"] for row in rows if row.get("accepted"))
        self.assertEqual(calls, 4)
        self.assertEqual(efforts, ["medium", "medium", "high", "high"])
        self.assertEqual([row["source"] for row in seen[1:]], [best_source] * 3)
        self.assertEqual(result["deliverable_fom"], 0.5)

    def test_escape_prompt_marks_high_effort_iterations(self):
        seen = []
        _, rows, _, efforts = self.run_mocked(
            adaptive=FAST_ADAPTIVE, observations=seen, n_iters=3
        )
        iterations = [row for row in rows if row["event"] == "iteration"]
        self.assertEqual(efforts, ["medium", "medium", "high"])
        self.assertEqual([row["escape"] for row in iterations], [False, False, True])
        self.assertIn("stuck in a local optimum", seen[2]["prompt"])
        self.assertEqual(iterations[2]["outcome"], "high-creative (no improvement)")

    def test_start_high_begins_with_an_escape_burst(self):
        seen = []
        _, rows, _, efforts = self.run_mocked(
            adaptive=dict(FAST_ADAPTIVE, high_iters=100),
            start_high=True,
            observations=seen,
            n_iters=3,
            areas=[10, 20, 5, 20],
        )
        iterations = [row for row in rows if row["event"] == "iteration"]
        # High until the first improvement (iteration 2), then medium.
        self.assertEqual(efforts, ["high", "high", "medium"])
        self.assertEqual([row["escape"] for row in iterations], [True, True, False])
        self.assertIn("stuck in a local optimum", seen[1]["prompt"])
        self.assertEqual(iterations[0]["outcome"], "high-creative (no improvement)")

    def test_notes_open_each_session_and_follow_a_compaction(self):
        seen = []
        _, rows, _, _ = self.run_mocked(
            notes="Direction A.", observations=seen, n_iters=4, compacted_calls=(2,)
        )
        starts = [
            call["prompt"].startswith("[CANDIDATE OPTIMIZATION DIRECTIONS]")
            for call in seen
        ]
        # Call 1 opens the session; call 3 follows the compaction of call 2.
        self.assertEqual(starts, [True, False, True, False])
        self.assertTrue(all("Direction A." in seen[i]["prompt"] for i in (0, 2)))
        iterations = [row for row in rows if row["event"] == "iteration"]
        self.assertEqual([row["md_sent"] for row in iterations], [True, False, True, False])

    def test_removed_cli_options_are_rejected(self):
        removed_options = (
            ["--effort-ladder"],
            ["--md-iter1"],
            ["--resume-run", "run"],
            ["--best-of-seeds"],
            ["--seeds", "2"],
            ["--seed-iters", "1"],
            ["--early-stop-valid", "4"],
            ["--adaptive-pressure", "1"],
            ["--adaptive-warmup-low", "1"],
            ["--adaptive-budget-frac", "0.5"],
            ["--sec"],
            ["--skip-gate-verify"],
            ["--allow-vacuous"],
            ["--effort", "max"],
            ["--effort", "xhigh"],
            ["--effort", "minimal"],
            ["--md-once", "notes.md"],
            ["--adaptive-high-prompt", "normal"],
        )
        for options in removed_options:
            with (
                self.subTest(options=options),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as raised:
                    optimize.argument_parser().parse_args(options)
                self.assertEqual(raised.exception.code, 2)

    def test_search_modes_and_notes_modes_parse(self):
        parser = optimize.argument_parser()
        fixed = parser.parse_args(["--effort", "low", "--md", "notes.md"])
        self.assertEqual(fixed.effort, "low")
        self.assertFalse(fixed.adaptive_effort)
        self.assertEqual(fixed.md, "notes.md")
        adaptive = parser.parse_args(
            ["--adaptive-effort", "--start-high", "--md", "notes.md", "--sec-plus-tb"]
        )
        self.assertTrue(adaptive.adaptive_effort)
        self.assertTrue(adaptive.start_high)
        self.assertTrue(adaptive.sec_plus_tb)

    def test_adaptive_defaults_are_the_paper_constants_of_each_provider(self):
        parser = optimize.argument_parser()
        claude = optimize._adaptive_settings(
            parser, parser.parse_args(["--adaptive-effort"])
        )
        self.assertEqual(
            claude, dict(patience=3, fail_weight=2.8, reset_rho=0.05, high_iters=1)
        )
        codex = optimize._adaptive_settings(
            parser, parser.parse_args(["--adaptive-effort", "--provider", "codex"])
        )
        self.assertEqual(
            codex, dict(patience=4, fail_weight=4.0, reset_rho=0.1, high_iters=1)
        )
        override = optimize._adaptive_settings(
            parser,
            parser.parse_args(
                ["--adaptive-effort", "--provider", "codex", "--adaptive-patience", "2"]
            ),
        )
        self.assertEqual(override["patience"], 2)
        self.assertEqual(override["fail_weight"], 4.0)

    def test_adaptive_options_require_adaptive_effort(self):
        parser = optimize.argument_parser()
        for options in (
            ["--adaptive-patience", "3"],
            ["--adaptive-reset-rho", "0"],
            ["--start-high"],
        ):
            with (
                self.subTest(options=options),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    optimize._adaptive_settings(parser, parser.parse_args(options))
        self.assertIsNone(optimize._adaptive_settings(parser, parser.parse_args([])))

    def test_plan_of_the_example_makes_no_tool_or_model_call(self):
        output = io.StringIO()
        argv = [
            "optimize.py",
            "--config",
            str(ROOT / "examples" / "designs.json"),
            "--adaptive-effort",
            "--plan",
        ]
        # main() sets module settings; restore them for the other tests.
        saved = {name: getattr(optimize, name) for name in MAIN_SETTINGS}
        try:
            with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                self.assertEqual(optimize.main(), 0)
        finally:
            for name, value in saved.items():
                setattr(optimize, name, value)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["designs"], ["sad4"])
        self.assertEqual(plan["settings"]["adaptive"]["patience"], 3)
        self.assertEqual(plan["settings"]["vectors"], 10000)


    def test_model_runs_require_the_launcher_but_a_baseline_does_not(self):
        config = str(ROOT / "examples" / "designs.json")
        environment = {k: v for k, v in os.environ.items() if k != "ARES_BOX_ENV"}
        saved = {name: getattr(optimize, name) for name in MAIN_SETTINGS}
        calls = []

        def fake_run(design, *args, **kwargs):
            calls.append(design.key)
            return {"design": design.key, "status": "ok"}

        try:
            with tempfile.TemporaryDirectory() as tmp, patch.dict(
                os.environ, environment, clear=True
            ):
                # Iterations make model calls, so they need the launcher.
                argv = ["optimize.py", "--config", config, "--iters", "2", "--out", tmp]
                with patch.object(sys, "argv", argv), contextlib.redirect_stderr(
                    io.StringIO()
                ):
                    with self.assertRaises(SystemExit):
                        optimize.main()
                # A baseline measurement makes no model call.
                argv = ["optimize.py", "--config", config, "--iters", "0", "--out", tmp]
                with patch.object(sys, "argv", argv), patch.object(
                    optimize, "run_one_design", side_effect=fake_run
                ), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(optimize.main(), 0)
            self.assertEqual(calls, ["sad4"])
        finally:
            for name, value in saved.items():
                setattr(optimize, name, value)

if __name__ == "__main__":
    unittest.main()
