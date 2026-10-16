#!/usr/bin/env python3.11
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Stage and run the hardware flow for one design.

The helper copies the flow scripts (flow/common plus flow/timing or flow/sec),
the design sources, the testbenches, and config.sh into a new work directory,
then runs out_loop.sh there: RTL verification, synthesis, gate verification,
switching-activity simulation, and power analysis. The optimization loop
calls run_synthesis(); the command line runs the flow on a prepared design.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLEAN_FRAMEWORK = Path(os.environ.get("RTL_FLOW_FRAMEWORK", HERE / "flow" / "timing"))
# Each run receives its own directory. The prefix may point to scratch storage.
WORK_BASE = str(Path(os.environ.get("RTL_FLOW_WORK_BASE", HERE / ".work" / "run")))
VALID_STEPS = ("verify", "syn", "verify_syn", "sim_syn", "pt")

# The bundled NanGate45 library is the default technology. It applies only
# when none of these three settings is set.
DEFAULT_TECHNOLOGY = {
    "RTL_FLOW_TECH_DIR": str(HERE / "tech" / "nangate45"),
    "RTL_FLOW_SYNTH_LIBS": "NangateOpenCellLibrary_typical.lib",
    "RTL_FLOW_SIM_MODELS": "NangateOpenCellLibrary.v",
}


def flow_environment() -> dict[str, str]:
    """Return the environment of a flow run, with the default technology and library cache."""
    env = dict(os.environ)
    if not any(env.get(name) for name in DEFAULT_TECHNOLOGY):
        env.update(DEFAULT_TECHNOLOGY)
    env.setdefault("RTL_FLOW_TECH_CACHE", str(HERE / ".work" / "technology_cache"))
    return env


@dataclass
class SynthesisResult:
    """Result of one flow run: the CSV path, the run ID, and the failed step."""
    csv_path: str
    run_id: str
    return_code: int
    failed_step: str = ""
    failed_run_id: str = ""


def run_synthesis(
    src_dir: str,
    tb_dir: str,
    config_file: str,
    output_dir: str = ".",
    run_synthesis_script: bool = True,
    cleanup_on_success: bool = True,
    skip_verify: bool = False,
    skip_verify_syn: bool = False,
    verify_only: bool = False,
    resume_run_id: str = "",
    start_step: str = "",
) -> SynthesisResult:
    """Stage a work directory and run the flow; a failed work directory stays for inspection."""
    run_id = resume_run_id or uuid.uuid4().hex[:12]
    work_dir = Path(f"{WORK_BASE}_{run_id}").resolve()
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError(
                "run ID must contain only letters, digits, underscores, or hyphens"
            )
        if start_step and start_step not in VALID_STEPS:
            raise ValueError(f"unknown start step: {start_step}")
        if start_step and not resume_run_id:
            raise ValueError("start_step requires resume_run_id")
        if verify_only and skip_verify:
            raise ValueError("verify_only and skip_verify cannot be combined")
        if resume_run_id:
            if not (work_dir / "out_loop.sh").is_file():
                raise ValueError(f"saved run not found: {work_dir}")
        else:
            framework = Path(CLEAN_FRAMEWORK).resolve()
            common = framework.parent / "common"
            if (
                not (common / "runner.py").is_file()
                or not (framework / "out_loop.sh").is_file()
            ):
                raise ValueError(f"invalid framework: {framework}")
            work_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(common, work_dir)
            shutil.copytree(framework, work_dir, dirs_exist_ok=True)
            shutil.copytree(src_dir, work_dir / "rtl" / "src", dirs_exist_ok=True)
            shutil.copytree(
                tb_dir, work_dir / "rtl" / "tb" / "templates", dirs_exist_ok=True
            )
            shutil.copy2(config_file, work_dir / "config.sh")
        print(f"[{run_id}] Work directory: {work_dir}", flush=True)
        if not run_synthesis_script:
            return SynthesisResult("", run_id, 0)
        command = ["bash", str(work_dir / "out_loop.sh")]
        if verify_only:
            command.append("--verify-only")
        if skip_verify:
            command.append("--skip-verify")
        if skip_verify_syn:
            command.append("--skip-verify-syn")
        if start_step:
            command.append(f"--start-step={start_step}")
        proc = subprocess.run(
            command, cwd=work_dir, check=False, env=flow_environment()
        )
        status_file = work_dir / "status.txt"
        status = status_file.read_text().strip() if status_file.is_file() else ""
        if proc.returncode or status != "COMPLETE":
            fields = status.split()
            failed = fields[-1] if fields and fields[0] == "FAILED" else "flow"
            return SynthesisResult("", run_id, proc.returncode or 1, failed, "run_001")
        csv_path = ""
        if not verify_only:
            destination = Path(output_dir).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            csv = destination / f"output_{run_id}.csv"
            shutil.copy2(work_dir / "results" / "output.csv", csv)
            csv_path = str(csv)
        if cleanup_on_success:
            shutil.rmtree(work_dir)
        return SynthesisResult(csv_path, run_id, 0)
    except (OSError, ValueError, shutil.Error) as exc:
        print(f"[{run_id}] Flow setup failed: {exc}", flush=True)
        return SynthesisResult("", run_id, 1, "setup")


def main() -> int:
    """Run the flow on a prepared design directory from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "design_dir",
        nargs="?",
        help="Directory with src/, tb/templates/, and config.sh",
    )
    parser.add_argument(
        "--transfer-only",
        action="store_true",
        help="Prepare a work directory without running tools",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Run RTL simulation or formal equivalence only",
    )
    parser.add_argument(
        "--skip-verify", action="store_true", help="Skip the RTL verification stage"
    )
    parser.add_argument(
        "--skip-verify-syn",
        action="store_true",
        help="Skip gate-level functional verification",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Retain the work directory after success",
    )
    parser.add_argument(
        "--resume",
        default="",
        metavar="RUN_ID",
        help="Reuse an existing work directory",
    )
    parser.add_argument(
        "--start-step",
        choices=VALID_STEPS,
        default="",
        help="Begin a resumed run at this stage",
    )
    parser.add_argument("--output-dir", help="Directory for the result CSV")
    args = parser.parse_args()
    if not args.resume and not args.design_dir:
        parser.error("design_dir is required for a new run")
    if args.start_step and not args.resume:
        parser.error("--start-step requires --resume")
    design = Path(args.design_dir or ".").resolve()
    result = run_synthesis(
        str(design / "src"),
        str(design / "tb" / "templates"),
        str(design / "config.sh"),
        output_dir=args.output_dir or str(design),
        run_synthesis_script=not args.transfer_only,
        cleanup_on_success=not args.no_cleanup,
        skip_verify=args.skip_verify,
        skip_verify_syn=args.skip_verify_syn,
        verify_only=args.verify_only,
        resume_run_id=args.resume,
        start_step=args.start_step,
    )
    if result.csv_path:
        print(f"Results: {result.csv_path}")
    if result.return_code:
        print(f"Run {result.run_id} failed at {result.failed_step}")
    return result.return_code


if __name__ == "__main__":
    raise SystemExit(main())
