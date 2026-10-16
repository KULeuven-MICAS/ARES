# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""The prompts must stay identical to the prompts of the paper runs.

tests/data/paper_prompts.json holds prompts that the experiment code of the
paper produced for fixed inputs. A change of a single character fails here.
"""

import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import optimize

PAPER = json.loads((ROOT / "tests" / "data" / "paper_prompts.json").read_text())
INPUTS = PAPER["inputs"]
DESIGN = types.SimpleNamespace(top=INPUTS["top"], key=INPUTS["top"])
RTL = INPUTS["rtl"]
METRICS = INPUTS["metrics"]


class PaperPromptTests(unittest.TestCase):
    def setUp(self):
        # The paper runs used 10,000 verification vectors and the bundled library.
        for name, value in (
            ("VECTORS", 10000),
            ("RESULTS_LABEL", "Synopsys Design Compiler + Nangate 45nm"),
            ("_NOTES", INPUTS["notes"]),
        ):
            patcher = patch.object(optimize, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_system_prompt(self):
        self.assertEqual(optimize._SYSTEM_PROMPT, PAPER["system_prompt"])

    def test_first_prompt(self):
        self.assertEqual(
            optimize.build_prompt(DESIGN, RTL, "sad4.v", METRICS, 1.0),
            PAPER["first_prompt"],
        )
        self.assertEqual(
            optimize.build_prompt(
                DESIGN, RTL, "sad4.v", dict(METRICS, timing_ns=None), 0.9
            ),
            PAPER["first_prompt_no_timing"],
        )

    def test_follow_up_prompts(self):
        build = optimize.build_followup
        self.assertEqual(
            build("rejected (no improvement)", METRICS, 1.0123, "v2", 0.8765),
            PAPER["followup_rejected"],
        )
        self.assertEqual(
            build("rejected (no improvement)", METRICS, 1.0123, "v2", 0.8765, escape=True),
            PAPER["followup_escape"],
        )
        self.assertEqual(
            build(
                "rtl-verify-fail (discarded)",
                None,
                None,
                "v1",
                0.95,
                flow_error="** Error: line 3\nMISMATCH cyc=4",
                best_rtl=RTL,
                best_primary_file="sad4.v",
            ),
            PAPER["followup_flow_error_resend"],
        )
        self.assertEqual(
            build(
                "accepted",
                METRICS,
                0.9,
                "v3",
                0.9,
                best_rtl=RTL,
                best_primary_file="sad4.v",
                compacted=True,
            ),
            PAPER["followup_compacted"],
        )

    def test_notes_block(self):
        self.assertEqual(optimize._notes_block(), PAPER["notes_block"])

    def test_vector_count_follows_the_setting(self):
        with patch.object(optimize, "VECTORS", 100000):
            self.assertIn("100,000-vector", optimize._notes_block())
            self.assertIn(
                "100000 random input vectors",
                optimize.build_prompt(DESIGN, RTL, "sad4.v", METRICS, 1.0),
            )


if __name__ == "__main__":
    unittest.main()
