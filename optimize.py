#!/usr/bin/env python3.11
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""ARES optimization loop.

Each iteration asks a language model for a better version of the current best
RTL source, checks the reply for equivalence, synthesizes it, measures area,
power, and delay, and accepts it only when the figure of merit (FoM) improves.
Model calls run in an Apptainer container (see box_claude.sh and box_codex.sh);
the hardware tools run on the host.
"""
from __future__ import annotations

import argparse
import csv as _csv
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness
from designs import Design, load_designs
from repair_session import repair as _repair_session

# ---------------------------------------------------------------------------
# Claude session repair
# ---------------------------------------------------------------------------
# After an automatic context compaction, the Claude CLI can refuse to resume a
# session that holds modified thinking blocks. The loop then repairs the
# session file and resumes it.
_CLAUDE_HOME = os.environ.get("ARES_CLAUDE_HOME") or os.path.expanduser("~/.claude")
_RESUME_POISON_RE = re.compile(
    r"thinking.*block.*cannot be modified|must remain as they were", re.I
)


def _session_jsonl(sid: str | None) -> Path | None:
    """Return the Claude session file of a session ID, if it exists."""
    if not sid:
        return None
    hits = glob.glob(os.path.join(_CLAUDE_HOME, "projects", "*", f"{sid}.jsonl"))
    return Path(hits[0]) if hits else None


def repair_resume_session(sid: str | None, why: str) -> bool:
    """Remove the thinking blocks that stop a session resume; keep a backup."""
    sess = _session_jsonl(sid)
    if sess is None or not sess.is_file():
        return False
    bak = sess.with_suffix(".jsonl.prerepair.bak")
    if not bak.exists():
        shutil.copy2(sess, bak)
    tmp = sess.with_suffix(".jsonl.repaired")
    stats = _repair_session(str(sess), str(tmp))
    os.replace(tmp, sess)
    log(
        f"[repair] {why}: session {sid[:8]} sanitized ({stats['blocks_stripped']} "
        f"thinking block(s) stripped, {stats['nodes_dropped']} node(s) dropped); "
        f"retrying resume with the repaired session."
    )
    return True


# ---------------------------------------------------------------------------
# Run settings (main() sets the values that the command line selects)
# ---------------------------------------------------------------------------
MODEL = os.environ.get("RTL_FLOW_CLAUDE_MODEL", "claude-opus-4-6")
EFFORT = "high"
EFFORT_LEVELS = ("low", "medium", "high")
MAX_OUTPUT_TOKENS = 128000
ITERS_DEFAULT = 25
ITERS_BUDGET_BACKSTOP = 100  # iteration cap when --max-cost sets the stop
VECTORS = harness.N_VECTORS_DEFAULT
RESEND_AFTER_FAILS = 2  # resend the best source after this many failed candidates
_RUN_SETTINGS: dict = {}  # recorded with the baseline of every run
_REGISTRY_SHA256: str | None = None
_CALL_COST_CAP: float | None = None  # --max-cost, also checked between Codex retries

# Adaptive-effort defaults per provider: the constants of the paper for Claude
# Opus 4.6 and for GPT-5.5 (Codex).
ADAPT_DEFAULTS = {
    "claude": {"patience": 3, "fail_weight": 2.8, "reset_rho": 0.05, "high_iters": 1},
    "codex": {"patience": 4, "fail_weight": 4.0, "reset_rho": 0.1, "high_iters": 1},
}


def _rho_reset(counter: float, gain: float, rho: float) -> float:
    """Reduce the stall counter after an improvement of relative size gain."""
    if rho <= 0.0:
        return 0.0
    if rho == float("inf"):
        return counter
    return counter * max(0.0, 1.0 - gain / rho)


# ---------------------------------------------------------------------------
# Model-call limits and stream parsing
# ---------------------------------------------------------------------------
CALL_TIMEOUT_SECS = 10000
CALL_TIMEOUT_SECS_HIGH = 5400
HEARTBEAT_SECS = 30
_LIMIT_RETRY_SECS = 3600  # wait time after a provider usage limit
_LIMIT_RE = re.compile(
    r"usage limit|rate.?limit|limit reached|will reset|resets? at|too many requests|\b429\b|quota",
    re.IGNORECASE,
)
MAX_CONSECUTIVE_CAP_HITS = 3  # stop after this many replies cut at the output limit


def _stream_events(out_text: str):
    """Yield the JSON events of a stream-json CLI output."""
    for line in (out_text or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _is_usage_limit(out_text: str, err_text: str) -> bool:
    """Return True when the CLI output reports a provider usage or rate limit."""
    texts = [err_text or ""]
    for line in (out_text or "").splitlines():
        if not line.strip().startswith("{"):
            texts.append(line)
    for ev in _stream_events(out_text):
        if ev.get("type") == "rate_limit_event":
            status = str((ev.get("rate_limit_info") or {}).get("status") or "")
            if status and (not status.startswith("allowed")):
                return True
        elif ev.get("type") == "result":
            texts.append(str(ev.get("result") or ""))
    return bool(_LIMIT_RE.search("\n".join(texts)))


def _stream_cap_hit(out_text: str) -> bool:
    """Return True when the reply stopped at the output-token limit."""
    for ev in _stream_events(out_text):
        if ev.get("type") == "result" and ev.get("stop_reason") == "max_tokens":
            return True
    return False


# ---------------------------------------------------------------------------
# Reply parsing: a ```thought``` block and a ```code``` block
# ---------------------------------------------------------------------------
_THOUGHT_RE = re.compile(r"```thought\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_CODE_RE = re.compile(r"```code\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_CODE_LANG_RE = re.compile(
    r"```(?:verilog|systemverilog|sv)\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE
)
_FENCE_ANY_RE = re.compile(r"```([A-Za-z0-9_+-]*)[^\n]*\n(.*?)\n```", re.DOTALL)
_MODULE_RE = re.compile(r"\bmodule\b.*?\bendmodule\b", re.DOTALL)


def extract_code(text: str) -> str | None:
    """Return the module source of a reply: the code fence first, then any module fence."""
    m = _CODE_RE.search(text)
    if m:
        return m.group(1)
    m = _CODE_LANG_RE.search(text)
    if m:
        return m.group(1)
    for fm in _FENCE_ANY_RE.finditer(text):
        if fm.group(1).lower() == "thought":
            continue
        body = fm.group(2)
        if _MODULE_RE.search(body):
            return body
    return None


# ---------------------------------------------------------------------------
# Prompts and optimization notes (the text is identical to the paper runs)
# ---------------------------------------------------------------------------
CODE_RETRY_MAX = 2  # requests for a missing code block per iteration
RESULTS_LABEL = os.environ.get(
    "RTL_FLOW_RESULTS_LABEL", "Synopsys Design Compiler + Nangate 45nm"
)
_SYSTEM_PROMPT = (
    "You are an expert Verilog design assistant. Your role is to address "
    "Verilog-related problems posed by the user. For each problem, you must provide "
    "both a 'thought' and the corresponding 'code'. The 'thought' is your conceptual "
    "idea for solving the problem. The 'code' is the Verilog implementation of your "
    "'thought'.\nStrictly format your response as follows:\n```thought\n[Your concise "
    "design idea (thought) here]\n```\n```code\n[Your complete, runnable Verilog "
    "implementation of the thought here]\n```\nYou have NO tools available: you cannot "
    "read files, run commands, or call any tool, and the complete current module is "
    "always provided to you inline in the conversation. Never emit a tool call and "
    "never pretend to read a file; work only from the module given to you. Label the "
    "implementation fence exactly ```code (not ```verilog or ```systemverilog), and "
    "always return the complete module from `module` to `endmodule` inside it."
)
# The --md notes open the first user message of each session and stay in the
# session history; the system prompt never holds them.
_NOTES: str | None = None  # text of the --md notes file
_NOTES_META: dict | None = None
_NOTES_CHECK = {"first_ctx": None, "sid": None, "done": False}
_NOTES_FRAMING_HEAD = (
    "The notes below are optimization DIRECTIONS that lowered the Figure of Merit "
    "on OTHER Verilog designs in the past. They are NOT facts about the design you "
    "are given, and are NOT guaranteed to help here: logic synthesis can respond very "
    "differently to the same change on a different design, so a direction that helped "
    "elsewhere may do nothing, or even make things worse, on this one. Treat each as a "
    "hypothesis to consider and test against THIS design's actual structure, not as a "
    "rule to apply. Use the ones that genuinely fit, adapt the idiom instead of copying "
    "it, and feel free to try the OPPOSITE of a suggested direction when that suits "
    "this design better. If a note is marked as a failed or avoid attempt (it did not "
    "improve FoM or broke equivalence), do NOT repeat it. "
)


def _notes_framing() -> str:
    """Return the instruction that comes before the notes in the prompt."""
    return _NOTES_FRAMING_HEAD + (
        "You must still return a COMPLETE module that is functionally equivalent to "
        "the current implementation; only strict FoM improvements that pass a "
        f"{VECTORS:,}-vector RTL and gate equivalence check are accepted."
    )


def _notes_block() -> str:
    """Return the notes block, or an empty string when no notes are set."""
    if not _NOTES:
        return ""
    return (
        "[CANDIDATE OPTIMIZATION DIRECTIONS]\n"
        + _notes_framing()
        + "\n\n"
        + _NOTES.strip()
        + "\n[END CANDIDATE OPTIMIZATION DIRECTIONS]"
    )


def _system_prompt_file(run_dir: Path) -> Path:
    """Write the system prompt file of the run and return its path."""
    sysp_file = run_dir / "_system_prompt.txt"
    if not sysp_file.exists():
        sysp_file.write_text(_SYSTEM_PROMPT)
    return sysp_file


def _notes_mode() -> str:
    """Describe the notes mode for the log and the records."""
    if not _NOTES:
        return "none"
    return "first user message of each session, kept in the session history"


# ---------------------------------------------------------------------------
# Provider settings
# ---------------------------------------------------------------------------
# Claude Code tools that every call denies: the model only reads and writes text.
_DENY_TOOLS = [
    "Agent",
    "TaskOutput",
    "Bash",
    "Glob",
    "Grep",
    "ExitPlanMode",
    "Read",
    "Edit",
    "Write",
    "NotebookEdit",
    "WebFetch",
    "TodoWrite",
    "WebSearch",
    "TaskStop",
    "AskUserQuestion",
    "Skill",
    "EnterPlanMode",
    "EnterWorktree",
    "ToolSearch",
    "CronCreate",
    "CronDelete",
    "CronList",
    "RemoteTrigger",
    "ExitWorktree",
    "DesignSync",
    "Monitor",
    "PushNotification",
    "ScheduleWakeup",
]
PROVIDER = "claude"
CODEX_HOME = None
CODEX_BIN = "codex"
CODEX_MODEL = os.environ.get("RTL_FLOW_CODEX_MODEL", "gpt-5.5")
_CODEX_DIR = Path(__file__).resolve().parent / "codex_backend"
LEAN = False  # True: delete harvested tool logs after each attempt


def _prune_iter_logs(iter_dir: Path) -> int:
    """Delete the harvested tool logs of one attempt; return the file count."""
    removed = 0
    for sub in ("synth_logs", "sec_logs"):
        p = iter_dir / sub
        if p.is_dir():
            removed += sum(1 for f in p.rglob("*") if f.is_file())
            shutil.rmtree(p, ignore_errors=True)
    return removed


# ---------------------------------------------------------------------------
# Run provenance: the timing constraints and the code that made a run
# ---------------------------------------------------------------------------
# The baseline synthesis script must constrain the clock and all input and
# output paths, so that the measured delay includes paths at the module ports.
_TIMING_COMMANDS = ("create_clock", "set_input_delay", "set_output_delay")
_TIMING_LINE_RE = re.compile(
    r"^\s*(create_clock|set_input_delay|set_output_delay)\b.*$", re.M
)


def _sha256_file(p: Path) -> str | None:
    """Return the SHA-256 checksum of a file, or None if it cannot be read."""
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _flow_proof(d: Design, run_dir: Path) -> dict:
    """Check and record that the baseline used the packaged flow and full timing constraints."""
    fw_t = Path(harness.CLEAN_FRAMEWORK_TIMING).resolve()
    fw_s = Path(harness.CLEAN_FRAMEWORK_SEC).resolve()
    fw_runner = Path(str(harness.synthesis.CLEAN_FRAMEWORK)).resolve()
    flow_root = (HERE / "flow").resolve()
    proof = {
        "ok": False,
        "why": "",
        "framework_timing": str(fw_t),
        "framework_sec": str(fw_s),
        "framework_runner": str(fw_runner),
        "flow_sha256": {
            str(p.relative_to(HERE)): _sha256_file(p)
            for p in [
                flow_root / "common" / "runner.py",
                flow_root / "common" / "syn" / "templates" / "synth.tcl",
            ]
        },
        "clocked": not d.combinational,
        "files": [],
        "constraints": [],
    }
    for fw in (fw_t, fw_s, fw_runner):
        if flow_root not in fw.parents:
            proof["why"] = f"framework {fw} is not the packaged flow in {flow_root}"
            return proof
    tcls = sorted((run_dir / "v0" / "synth_logs").glob("syn/logs/*/synth_filled.tcl"))
    if not tcls:
        proof["why"] = (
            "no v0 synth_filled.tcl under v0/synth_logs/syn/logs/; the flow cannot be proven"
        )
        return proof
    dest = run_dir / "v0" / "flow_proof"
    dest.mkdir(parents=True, exist_ok=True)
    missing = []
    for tcl in tcls:
        text = tcl.read_text(errors="replace")
        lines = [m.group(0).strip() for m in _TIMING_LINE_RE.finditer(text)]
        tag = tcl.parent.name
        shutil.copy2(tcl, dest / f"{tag}_synth_filled.tcl")
        proof["files"].append(f"v0/flow_proof/{tag}_synth_filled.tcl")
        proof["constraints"].append({"run": tag, "lines": lines})
        lacks = [k for k in _TIMING_COMMANDS if not any(ln.startswith(k) for ln in lines)]
        if lacks:
            missing.append(f"{tag} lacks {', '.join(lacks)}")
    if missing:
        proof["why"] = "; ".join(missing)
        return proof
    proof["ok"] = True
    proof["summary"] = (
        f"{len(tcls)} synth_filled.tcl file(s), each with create_clock, "
        f"set_input_delay, and set_output_delay; framework {fw_t.relative_to(HERE)}"
    )
    return proof


def _provenance(flow_proof: dict | None) -> dict:
    """Return the settings, checksums, and box details stored with the baseline record."""
    code = {
        n: _sha256_file(HERE / n) for n in ("optimize.py", "harness.py", "designs.py")
    }
    if PROVIDER == "codex":
        code["codex_backend/codex_call.py"] = _sha256_file(_CODEX_DIR / "codex_call.py")
    return {
        "flow": flow_proof,
        "box_env": os.environ.get("ARES_BOX_ENV"),
        "settings": dict(_RUN_SETTINGS),
        "config_sha256": _REGISTRY_SHA256,
        "provider": PROVIDER,
        "model": MODEL,
        "md_protocol": _notes_mode(),
        "code_sha256": code,
        "system_prompt_sha256": hashlib.sha256(_SYSTEM_PROMPT.encode()).hexdigest(),
    }


# ---------------------------------------------------------------------------
# Codex cost accounting (USD per token, loaded from the --prices file)
# ---------------------------------------------------------------------------
_CODEX_PRICE = None
_CODEX_PRICE_LONG = None
_CODEX_LONG_CTX = float("inf")


def _codex_cost(in_tok: int, cached_in: int, out_tok: int) -> float:
    """Compute the cost of one Codex call from its token counts."""
    if _CODEX_PRICE is None:
        raise RuntimeError(
            "Codex token prices must be configured with --prices for paid-call accounting."
        )
    p = _CODEX_PRICE_LONG if in_tok > _CODEX_LONG_CTX else _CODEX_PRICE
    fresh = max(0, in_tok - cached_in)
    return fresh * p["in"] + cached_in * p["cached"] + out_tok * p["out"]


_CODEX_LIMIT_RE = re.compile(r"rate.?limit|usage limit|quota|429|too many requests", re.I)


def _codex_module():
    """Import codex_backend/codex_call.py on first use."""
    if str(_CODEX_DIR) not in sys.path:
        sys.path.insert(0, str(_CODEX_DIR))
    import codex_call

    return codex_call


# ---------------------------------------------------------------------------
# Logging and measurements
# ---------------------------------------------------------------------------
_LOG_FH = None  # session.log of the current run


def log(msg: str) -> None:
    """Print a time-stamped line and copy it into the session log."""
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if _LOG_FH:
        _LOG_FH.write(line + "\n")
        _LOG_FH.flush()


def _new_totals() -> dict:
    """Return zero usage counters for one design."""
    return {
        "cost": 0.0,
        "in_tok": 0,
        "out_tok": 0,
        "think_tok_est": 0,
        "cache_read": 0,
        "cache_creat": 0,
        "calls": 0,
    }


def read_metrics(csv_path: Path) -> dict:
    """Read area, mean power over all workloads, and delay from the flow's CSV file."""
    rows = list(_csv.DictReader(csv_path.read_text().strip().split("\n")))
    if not rows:
        raise RuntimeError(f"no data rows in {csv_path}")
    row = rows[0]
    area = float(row["area"].strip())
    power_cols = [
        k for k in row if k.startswith("power_") and (not k.endswith("_hierarchy"))
    ]
    powers = [float(row[k].strip()) for k in power_cols if row[k].strip()]
    mean_power = sum(powers) / len(powers) if powers else float("nan")
    timing = None
    raw = (row.get("timing_ns") or "").strip()
    if raw:
        try:
            timing = float(raw)
        except ValueError:
            timing = None
    return {"area": area, "mean_power": mean_power, "timing_ns": timing}


def valid_metrics(metrics: dict, *, baseline: bool = False) -> bool:
    """Return True when area, power, and delay are finite (and positive for a baseline)."""
    keys = ("area", "mean_power", "timing_ns")
    return all(
        isinstance(metrics.get(key), (int, float))
        and math.isfinite(metrics[key])
        and (metrics[key] > 0 if baseline else metrics[key] >= 0)
        for key in keys
    )


def ppa_fom(m: dict, base: dict) -> float:
    """Equally weighted product of area, power, and delay ratios; lower wins."""
    if not valid_metrics(base, baseline=True):
        raise ValueError("Baseline area, power, and delay must be finite and positive.")
    if not valid_metrics(m):
        return float("inf")
    return math.prod(m[key] / base[key] for key in ("area", "mean_power", "timing_ns"))


# ---------------------------------------------------------------------------
# Flow-error feedback: the first error line of a failed attempt goes to the model
# ---------------------------------------------------------------------------
_SIM_ERROR_RE = re.compile(
    r"\*\* Error|\(vlog-|MISMATCH|FAILURES DETECTED|GOLDEN_SHORT|GOLDEN_OPEN_FAIL"
    r"|\bTIMEOUT\b|\bError:",
    re.IGNORECASE,
)
_FLOW_ERR_SOURCES = {
    "verify": (
        ["verify_rtl/logs/verify_*.log", "verify_rtl/logs/verify_*.log.compile"],
        _SIM_ERROR_RE,
    ),
    "verify_syn": (
        [
            "verify_syn/logs/**/verify_syn_*.log",
            "verify_syn/logs/**/verify_syn_*.log.compile",
        ],
        _SIM_ERROR_RE,
    ),
    "syn": (["syn/logs/**/syn.log"], re.compile(r"\bError:|^Error\b", re.IGNORECASE)),
    "sim_syn": (
        ["sim-syn/logs/**/*.log"],
        re.compile(r"\*\* Error|MISMATCH|\bError:|\bTIMEOUT\b", re.IGNORECASE),
    ),
    "pt": (
        ["pt/terminal_logs/**/*.log", "pt/reports/**/*.rpt"],
        re.compile(r"\bError:|\*\* Error", re.IGNORECASE),
    ),
}


def extract_flow_error(
    logs_dir: Path,
    failed_step: str,
    use_sec: bool = False,
    ctx: int = 1,
    max_chars: int = 240,
) -> str | None:
    """Return the first error line of a failed step with one line of context."""
    globs, pat = _FLOW_ERR_SOURCES.get(failed_step, (None, None))
    if use_sec and failed_step == "verify":
        globs = ["verify_rtl/logs/**/*.log", "**/sec*.log", "**/jg*.log"]
        pat = re.compile(
            r"not\s+equivalent|counter-?example|proven\s+FAIL|\bError\b|\bfail",
            re.IGNORECASE,
        )
    if not globs or not logs_dir.is_dir():
        return None
    for g in globs:
        for f in sorted(logs_dir.glob(g)):
            if not f.is_file():
                continue
            try:
                lines = f.read_text(errors="replace").splitlines()
            except OSError:
                continue
            for idx, ln in enumerate(lines):
                if pat.search(ln):
                    lo, hi = (max(0, idx - ctx), min(len(lines), idx + ctx + 1))
                    chunk = [l.rstrip()[:max_chars] for l in lines[lo:hi]]
                    excerpt = "\n".join(chunk).strip()
                    if excerpt:
                        return excerpt
    return None


# ---------------------------------------------------------------------------
# User prompts
# ---------------------------------------------------------------------------
def build_prompt(
    d: Design, rtl_text: str, primary_file: str, m: dict, fom: float
) -> str:
    """Return the first prompt of a session: goal, constraints, source, and results."""
    timing_str = f"{m['timing_ns']:.4f} ns" if m["timing_ns"] is not None else "n/a"
    constraints = [
        f"  - The top module's NAME ({d.top}), its PORT names, and PORT widths MUST "
        "stay exactly the same. Functional equivalence to the original is checked "
        f"with {VECTORS} random input vectors (at RTL and gate level); any change in "
        "behavior or interface is rejected."
    ]
    return "\n".join(
        [
            f"Optimize the Verilog design below (file: {primary_file}, top module: {d.top}).",
            "",
            "GOAL: minimize the Figure of Merit",
            "    FoM = (area / area_v0) * (power / power_v0) * (timing / timing_v0)",
            "where each term is normalized to the original baseline. Lower is better; "
            "the baseline scores 1.000. Push the FoM as low as you can and as "
            "cost-efficiently as possible: you have a limited iteration budget, so when "
            "several independent optimizations are each safe and beneficial, apply them "
            "together in one candidate rather than one per iteration.",
            "",
            "HARD CONSTRAINTS:",
            *constraints,
            "  - Return the COMPLETE replacement for this file — it overwrites the file "
            "verbatim. Do not omit anything; do not add other files.",
            "",
            "CURRENT IMPLEMENTATION (this is the best version so far):",
            "```verilog",
            rtl_text.rstrip(),
            "```",
            "",
            f"CURRENT MEASURED RESULTS ({RESULTS_LABEL}):",
            f"  area   = {m['area']:.4f}",
            f"  power  = {m['mean_power']:.6g}",
            f"  timing = {timing_str}",
            f"  FoM    = {fom:.4f}  (vs 1.000 baseline)",
            "",
            "Reply with your idea in a ```thought``` block followed by the full "
            "optimized module in a ```code``` block.",
        ]
    )


def build_followup(
    prev_outcome: str,
    prev_m: dict | None,
    prev_fom,
    best_label: str,
    best_fom: float,
    escape: bool = False,
    flow_error: str | None = None,
    best_rtl: str | None = None,
    best_primary_file: str | None = None,
    compacted: bool = False,
) -> str:
    """Return a follow-up prompt: the last outcome and the next instruction.

    escape=True selects the "escape" prompt of an adaptive high-effort burst,
    which asks for a structural change instead of a small step.
    """
    if prev_m is not None:
        ts = (
            f"{prev_m['timing_ns']:.4f} ns"
            if prev_m["timing_ns"] is not None
            else "n/a"
        )
        res = (
            f"Result of your previous candidate: area={prev_m['area']:.4f}  "
            f"power={prev_m['mean_power']:.6g}  timing={ts}  FoM={prev_fom:.4f}  ->  "
            f"{prev_outcome}."
        )
    else:
        res = f"Your previous candidate was discarded: {prev_outcome}."
    if escape:
        instr = (
            f"The current best is {best_label} at FoM={best_fom:.4f} (lower is better), "
            "but the optimization may be stuck in a local optimum. For THIS iteration, "
            "deliberately try to break out instead of making a safe incremental tweak: be "
            "bold and creative. Take one structural decision committed to earlier in this "
            "session (for example the adder organization, a mux/case structure, a state "
            "encoding, or a shared-vs-split resource choice) and CHANGE or REVERT it, even "
            "if it was previously accepted, to move into a different structural family; "
            "then stack at least one further optimization on top of that change. Do not "
            "hold back just because the result might not immediately beat the current "
            "best; the point is to explore a genuinely different direction that could lead "
            "somewhere better. You must still return a COMPLETE module that is "
            "functionally equivalent to the original."
        )
    else:
        instr = (
            f"The current best is {best_label} at FoM={best_fom:.4f} (lower is better). "
            "You have the full history of everything you have already tried this session "
            "— do not resubmit a change that already failed or was rejected; apply a "
            "further optimization, or a combination of several, on top of the "
            "current-best module."
        )
    parts = [res]
    if flow_error:
        parts += [
            "",
            "The flow rejected it. First error from the tool log (the failing line, "
            "with one line of context above and below):",
            "```",
            flow_error,
            "```",
            "Diagnose this specific error and either fix it in your next candidate or "
            "revert the change that caused it; do not repeat it.",
        ]
    if best_rtl is not None:
        fn = best_primary_file or "the file"
        if compacted:
            note = (
                "Note: the earlier turns of this session were automatically summarized "
                "(compacted), so some details may be lost. The module below is the "
                "current design to optimize; base your next candidate on this verbatim "
                "text, changing only what your optimization requires."
            )
        else:
            note = (
                f"The file has been reverted to the current best ({best_label}). This is "
                f"the EXACT current-best source ({fn}); base your next candidate on this "
                "verbatim text and change only what your optimization requires, so you "
                "do not reintroduce a transcription error elsewhere in the module:"
            )
        parts += ["", note, "```verilog", best_rtl.rstrip(), "```"]
    parts += [
        instr,
        "",
        "Reply with your idea in a ```thought``` block followed by the COMPLETE "
        "optimized module in a ```code``` block.",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Claude Code calls
# ---------------------------------------------------------------------------
def _feed_stdin(proc, text: str) -> None:
    """Write the prompt to the CLI's standard input in a background thread."""
    try:
        proc.stdin.write(text)
        proc.stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _run_claude_streaming(
    cmd: list[str],
    cwd: str,
    env: dict,
    jsonl_path: Path,
    total_timeout: int,
    heartbeat: int,
    stdin_text: str | None = None,
) -> tuple[int | None, str, str]:
    """Run one CLI call, save its event stream, and log a heartbeat; None = timeout."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL if stdin_text is None else subprocess.PIPE,
        bufsize=1,
    )
    if stdin_text is not None:
        threading.Thread(
            target=_feed_stdin, args=(proc, stdin_text), daemon=True
        ).start()
    out: list[str] = []
    err: list[str] = []
    lock = threading.Lock()
    jf = open(jsonl_path, "w")

    def _drain(stream, sink, to_file):
        for line in iter(stream.readline, ""):
            with lock:
                sink.append(line)
            if to_file:
                jf.write(line)
                jf.flush()

    threads = [
        threading.Thread(target=_drain, args=(proc.stdout, out, True), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err, False), daemon=True),
    ]
    for t in threads:
        t.start()
    start = time.time()
    next_hb = start + heartbeat
    timed_out = False
    while True:
        try:
            proc.wait(timeout=2)
            break
        except subprocess.TimeoutExpired:
            now = time.time()
            if now >= next_hb:
                with lock:
                    n = len(out)
                log(f"[claude] …running {int(now - start)}s, {n} stream line(s) so far")
                next_hb = now + heartbeat
            if now - start > total_timeout:
                log(
                    f"[claude] HARD TIMEOUT after {int(now - start)}s — killing this "
                    f"call and retrying."
                )
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                timed_out = True
                break
    for t in threads:
        t.join(timeout=5)
    jf.close()
    return (None if timed_out else proc.returncode, "".join(out), "".join(err))


def call_claude(
    prompt: str,
    run_dir: Path,
    tag: str,
    totals: dict,
    resume: str | None,
    effort: str | None = None,
    via_stdin: bool = False,
) -> tuple[str, str | None, dict]:
    """Call Claude Code with all tools disabled; return (reply, session ID, usage).

    The call resumes the session `resume` when it is set. Timeouts and transient
    errors are retried; a usage limit waits and retries; any tool use stops the run.
    """
    eff = effort or EFFORT
    settings = json.dumps(
        {
            "permissions": {"allow": [], "deny": _DENY_TOOLS},
            "autoMemoryEnabled": False,
            "disableBundledSkills": True,
            "env": {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(MAX_OUTPUT_TOKENS)},
        }
    )
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--settings",
        settings,
        "--effort",
        eff,
        "--allowedTools",
        "",
        "--tools",
        "",
        "--strict-mcp-config",
        "--model",
        MODEL,
    ]
    if via_stdin:
        del cmd[2]
    if resume:
        cmd += ["--resume", resume]
    sysp_file = _system_prompt_file(run_dir)
    cmd += ["--append-system-prompt-file", str(sysp_file)]
    env = os.environ.copy()
    jsonl_path = run_dir / f"claude_{tag}.jsonl"
    transient = 0
    poison_repaired = False
    while True:
        log(
            f"[claude] calling {MODEL} (tag={tag}, effort={eff}{(', resume' if resume else '')})..."
        )
        try:
            call_timeout = (
                CALL_TIMEOUT_SECS_HIGH if eff == "high" else CALL_TIMEOUT_SECS
            )
            if via_stdin:
                rc, out_text, err_text = _run_claude_streaming(
                    cmd,
                    str(run_dir),
                    env,
                    jsonl_path,
                    call_timeout,
                    HEARTBEAT_SECS,
                    stdin_text=prompt,
                )
            else:
                rc, out_text, err_text = _run_claude_streaming(
                    cmd, str(run_dir), env, jsonl_path, call_timeout, HEARTBEAT_SECS
                )
        except FileNotFoundError:
            log("[claude] ERROR: `claude` CLI not found on PATH.")
            sys.exit(1)
        cap_hit = bool(rc) and rc is not None and _stream_cap_hit(out_text)
        if rc == 0 or cap_hit:
            result_evt, text_parts = (None, [])
            init_tools = None
            tool_names = []
            compacted = False
            compact_pre_tokens = None
            think_chars = text_chars = think_blocks = tool_uses = 0
            for line in out_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    init_tools = ev.get("tools")
                elif (
                    ev.get("type") == "system"
                    and ev.get("subtype") == "compact_boundary"
                ):
                    compacted = True
                    meta = ev.get("compact_metadata") or {}
                    compact_pre_tokens = meta.get("pre_tokens")
                elif ev.get("type") == "assistant":
                    for b in ev.get("message", {}).get("content", []):
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "thinking":
                            think_blocks += 1
                            think_chars += len(b.get("thinking", "") or "")
                        elif b.get("type") == "text":
                            t = b.get("text", "") or ""
                            text_chars += len(t)
                            text_parts.append(t)
                        elif b.get("type") == "tool_use":
                            tool_uses += 1
                            tool_names.append(b.get("name"))
                elif ev.get("type") == "result":
                    result_evt = ev
            if result_evt is None:
                if init_tools or tool_uses:
                    raise RuntimeError("Model reply violated tool isolation.")
                raise RuntimeError(
                    "Claude returned no result event; usage and reply cannot be validated."
                )
            num_turns = result_evt.get("num_turns")
            denials = result_evt.get("permission_denials") or []
            violations = []
            if init_tools:
                violations.append(f"CLI loaded {len(init_tools)} tool(s): {init_tools}")
            if tool_uses:
                violations.append(f"{tool_uses} tool_use block(s): {tool_names}")
            if denials:
                violations.append(f"{len(denials)} permission denial(s)")
            if violations:
                log("[claude] NO-TOOLS INTEGRITY VIOLATION — " + "; ".join(violations))
                log(
                    "[claude] Refusing to generate tool-contaminated test data. The CLI "
                    "may have added tools not in _DENY_TOOLS, or an MCP server became "
                    "available. Fix the deny list / disable MCP, then re-run. "
                    "(init.tools must be [])."
                )
                sys.exit(2)
            if compacted:
                log(
                    f"[claude] auto-compaction occurred (tag={tag}, "
                    f"pre_tokens={compact_pre_tokens}, num_turns={num_turns}); context "
                    f"summarized by the CLI — this iteration is LABELED compacted in "
                    f"its usage record."
                )
            elif isinstance(num_turns, int) and num_turns > 1:
                log(
                    f"[claude] note: num_turns={num_turns} with no compaction and no "
                    f"tool use (likely an output-length continuation)."
                )
            usage = result_evt.get("usage") or {}
            cost = result_evt.get("total_cost_usd") or result_evt.get("cost_usd")
            in_tok = int(usage.get("input_tokens") or 0)
            out_tok = int(usage.get("output_tokens") or 0)
            cache_read = int(usage.get("cache_read_input_tokens") or 0)
            cache_creat = int(usage.get("cache_creation_input_tokens") or 0)
            call_cost = float(cost) if isinstance(cost, (int, float)) else 0.0
            think_tok_est = max(0, out_tok - round(text_chars / 4))
            call_usage = {
                "in_tok": in_tok,
                "out_tok": out_tok,
                "think_tok_est": think_tok_est,
                "cache_read": cache_read,
                "cache_creat": cache_creat,
                "cost": call_cost,
                "compacted": compacted,
                "compact_pre_tokens": compact_pre_tokens,
                "num_turns": num_turns,
                "cap_hit": cap_hit,
            }
            if cap_hit:
                log(
                    f"[claude] OUTPUT LIMIT: the call ended on max_tokens "
                    f"({MAX_OUTPUT_TOKENS} per request) with no answer; its cost is "
                    f"recorded and the iteration counts as a failure. The next "
                    f"iteration starts a new session with the full prompt."
                )
            for k in (
                "in_tok",
                "out_tok",
                "think_tok_est",
                "cache_read",
                "cache_creat",
                "cost",
            ):
                totals[k] += call_usage[k]
            totals["calls"] += 1
            log(
                f"[claude] tokens in:{in_tok} out:{out_tok} (thinking~{think_tok_est}; "
                f"{think_blocks} block(s))  cache(read:{cache_read} "
                f"write:{cache_creat})  "
                f"cost={('$%.4f' % call_cost if cost is not None else 'n/a')}  | "
                f"design-total: ${totals['cost']:.4f} over {totals['calls']} call(s)"
            )
            sid = result_evt.get("session_id")
            text = (result_evt.get("result", "") or "").strip() or "".join(text_parts)
            if compacted and sid:
                repair_resume_session(sid, "post-compaction proactive")
            return (text, sid, call_usage)
        combined = (err_text or "") + "\n" + (out_text or "")
        err = combined.strip()[:200]
        if rc is None:
            transient += 1
            if transient >= 3:
                log(f"[claude] giving up after {transient} timeout(s)/failure(s).")
                sys.exit(1)
            log(f"[claude] timed out — retry {transient}/3 in 30s")
            time.sleep(30)
            continue
        if _is_usage_limit(out_text, err_text):
            mins = _LIMIT_RETRY_SECS // 60
            log(
                f"[claude] usage/session limit reached — sleeping {mins} min, retrying "
                f"hourly until it clears. ({err})"
            )
            time.sleep(_LIMIT_RETRY_SECS)
            continue
        if _RESUME_POISON_RE.search(combined):
            if not poison_repaired and repair_resume_session(
                resume, "resume-poison 400"
            ):
                poison_repaired = True
                log("[claude] retrying resume after sanitizing the session...")
                continue
            log(f"[claude] resume-poison 400 not recoverable by repair: {err}")
            sys.exit(1)
        transient += 1
        if transient >= 3:
            log(f"[claude] giving up after {transient} transient failures: {err}")
            sys.exit(1)
        log(f"[claude] CLI error rc={rc} ({err}); retry {transient}/3 in 30s")
        time.sleep(30)


# ---------------------------------------------------------------------------
# Codex calls
# ---------------------------------------------------------------------------
def call_codex_provider(
    prompt: str,
    run_dir: Path,
    tag: str,
    totals: dict,
    resume: str | None,
    effort: str | None = None,
    via_stdin: bool = False,
) -> tuple[str, str | None, dict]:
    """Call the Codex CLI with no tools; return (reply, thread ID, usage of this call).

    The cost is computed from the token counts and the --prices file. An empty
    reply is retried up to three times; its tokens are still counted.
    """
    mod = _codex_module()
    eff = effort or EFFORT
    sysp_file = _system_prompt_file(run_dir)
    extra = ["-c", f'model_instructions_file="{sysp_file}"']
    stdin_kw = {"stdin_text": prompt} if via_stdin else {}
    transient = 0
    usage_before = dict(totals)

    def usage_delta():
        delta = {key: totals[key] - usage_before[key] for key in usage_before}
        delta.update(compacted=False, compact_pre_tokens=None, num_turns=None)
        return delta

    while True:
        log(
            f"[codex] calling {MODEL} (tag={tag}, effort={eff}{(', resume' if resume else '')})..."
        )
        try:
            rec = mod.call_codex(
                prompt,
                run_dir,
                tag,
                CODEX_HOME,
                effort=eff,
                model=MODEL,
                resume=resume,
                cwd=run_dir,
                timeout=CALL_TIMEOUT_SECS_HIGH if eff == "high" else CALL_TIMEOUT_SECS,
                heartbeat=HEARTBEAT_SECS,
                extra_args=extra,
                codex_bin=CODEX_BIN,
                env=os.environ.copy(),
                log=lambda m: log(m) if "...running" in m or "TIMEOUT" in m else None,
                **stdin_kw,
            )
        except mod.CodexError as exc:
            log(f"[codex] NO-TOOLS INTEGRITY VIOLATION - {exc}")
            log(
                "[codex] Rebuild the clean home with make_codex_env.py and re-run "
                "probe_codex.py before starting again."
            )
            sys.exit(2)
        except FileNotFoundError:
            log(f"[codex] ERROR: '{CODEX_BIN}' is not on PATH.")
            sys.exit(1)
        text = (rec["text"] or "").strip()
        tok = rec["tokens"]
        call_usage = {
            "in_tok": tok["in_tok"],
            "out_tok": tok["out_tok"],
            "think_tok_est": tok["reasoning_tok"],
            "cache_read": tok["cached_in_tok"],
            "cache_creat": 0,
            "cost": _codex_cost(tok["in_tok"], tok["cached_in_tok"], tok["out_tok"]),
        }
        for key, value in call_usage.items():
            totals[key] += value
        totals["calls"] += 1
        log(
            f"[codex] call cost ${call_usage['cost']:.4f} estimated; cumulative "
            f"${totals['cost']:.4f} across {totals['calls']} calls"
        )
        if text:
            return (text, rec["thread_id"], usage_delta())
        if _CALL_COST_CAP is not None and totals["cost"] >= _CALL_COST_CAP:
            return ("", rec.get("thread_id"), usage_delta())
        why = (
            rec["turn_failed"]
            or (rec["errors"] or [""])[-1]
            or rec.get("stderr")
            or "empty reply"
        )
        if _CODEX_LIMIT_RE.search(str(why)):
            mins = _LIMIT_RETRY_SECS // 60
            log(
                f"[codex] subscription limit reached - sleeping {mins} min, then "
                f"retrying. ({str(why)[:160]})"
            )
            time.sleep(_LIMIT_RETRY_SECS)
            continue
        transient += 1
        if transient >= 3:
            log(f"[codex] giving up after {transient} failure(s): {str(why)[:200]}")
            return ("", rec.get("thread_id"), usage_delta())
        log(f"[codex] no reply ({str(why)[:160]}); retry {transient}/3 in 30s")
        time.sleep(30)


# ---------------------------------------------------------------------------
# Notes check: prove that the notes stay in the session context
# ---------------------------------------------------------------------------
def _context_tokens(call_usage: dict) -> int:
    """Return the input-context size of one call in tokens."""
    u = call_usage or {}
    if PROVIDER == "codex":
        return int(u.get("in_tok") or 0)
    return (
        int(u.get("in_tok") or 0)
        + int(u.get("cache_read") or 0)
        + int(u.get("cache_creat") or 0)
    )


def _codex_store() -> Path | None:
    """Return the Codex home of this run (the launcher sets CODEX_HOME)."""
    env = os.environ.get("CODEX_HOME")
    return Path(env) if env else CODEX_HOME


def _history_file(sid: str | None) -> Path | None:
    """Return the provider's history file of a session."""
    if not sid:
        return None
    if PROVIDER == "codex":
        store = _codex_store()
        if store is None:
            return None
        hits = glob.glob(
            os.path.join(str(store), "sessions", "**", f"rollout-*{sid}.jsonl"),
            recursive=True,
        )
        return Path(sorted(hits)[-1]) if hits else None
    return _session_jsonl(sid)


def _json_strings(obj):
    """Yield every string inside a parsed JSON value."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _json_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _json_strings(v)


def _history_holds_notes(path: Path) -> bool:
    """Return True when a session history file holds the complete notes block."""
    block = _notes_block()
    for line in path.read_text(errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if any(block in s for s in _json_strings(ev)):
            return True
    return False


def _notes_check(
    call_usage: dict, resume: str | None, sid: str | None, md_sent: bool
) -> None:
    """Stop the run when the notes are not in the session history.

    After the first call, the history file must hold the notes. The first
    resumed call must use at least as many input tokens as the first call.
    """
    st = _NOTES_CHECK
    if md_sent and resume is None:
        if not sid:
            log(
                "[notes] no session ID was returned; the next call starts a new "
                "session with the notes again."
            )
            return
        path = _history_file(sid)
        if path is None or not path.is_file():
            log(
                f"[notes] FAILED: no history file for session {sid} (store "
                f"{(_codex_store() if PROVIDER == 'codex' else _CLAUDE_HOME)}). Stop: "
                f"the run cannot prove that the notes stay in context."
            )
            sys.exit(3)
        if not _history_holds_notes(path):
            log(
                f"[notes] FAILED: the session history {path.name} does not hold the "
                f"complete notes block. Stop: the notes would not stay in context."
            )
            sys.exit(3)
        st.update(
            first_ctx=_context_tokens(call_usage),
            sid=sid,
            done=False,
            first_turns=(call_usage or {}).get("num_turns") or 1,
        )
        log(
            f"[notes] check passed: the session history {path.name} holds the notes "
            f"({len(_notes_block())} chars); first call {st['first_ctx']} input tokens."
        )
        return
    if md_sent:
        log("[notes] the notes were sent again after a context compaction.")
        return
    if st["done"] or not resume or resume != st["sid"] or (not st["first_ctx"]):
        return
    st["done"] = True
    ctx = _context_tokens(call_usage)
    turns = (call_usage or {}).get("num_turns") or 1
    if (call_usage or {}).get("compacted") or turns > 1 or st["first_turns"] > 1:
        log(
            f"[notes] token check skipped: a call made more than one request or was "
            f"auto-compacted (first call {st['first_ctx']}, this call {ctx} input "
            f"tokens). The history check passed."
        )
        return
    if ctx < st["first_ctx"]:
        log(
            f"[notes] FAILED: the first resumed call used {ctx} input tokens, fewer "
            f"than the {st['first_ctx']} of the first call, so its context did not hold "
            f"the notes. Stop."
        )
        sys.exit(3)
    log(
        f"[notes] check passed: the first resumed call used {ctx} input tokens, at "
        f"least the {st['first_ctx']} of the first call, so the notes stayed in context."
    )


def call_model(
    prompt: str,
    run_dir: Path,
    tag: str,
    totals: dict,
    resume: str | None,
    effort: str | None = None,
    md_sent: bool = False,
) -> tuple[str, str | None, dict]:
    """Call the selected provider; md_sent=True sends the prompt through stdin."""
    if PROVIDER == "codex":
        out = call_codex_provider(
            prompt, run_dir, tag, totals, resume, effort, via_stdin=md_sent
        )
    else:
        out = call_claude(
            prompt, run_dir, tag, totals, resume, effort, via_stdin=md_sent
        )
    if _NOTES:
        _notes_check(out[2], resume, out[1], md_sent)
    return out


# ---------------------------------------------------------------------------
# Baseline setup
# ---------------------------------------------------------------------------
def setup_design(
    d: Design,
    design_dir: Path,
    v0_synth_out: Path,
    v0_logs: Path,
    n_vectors: int,
    sec_plus_tb: bool = False,
    v0_rtl: str = "",
) -> tuple[harness.DesignDirs, dict] | tuple[None, str]:
    """Build the work copy, the golden trace, and the baseline (v0) measurement.

    Return (design directories, baseline metrics), or (None, reason) on failure.
    """
    log(f"--- setup '{d.key}' (top {d.top}) ---")
    dirs = harness.build_design_dir(d, design_dir, n_vectors=n_vectors)
    if v0_rtl:
        seed = Path(v0_rtl)
        if not seed.is_file():
            return (None, f"--v0-rtl file not found: {seed}")
        dirs.primary_rtl.write_text(seed.read_text())
        log(
            f"[setup] v0 SEEDED from {seed} ({len(seed.read_text())} chars) -> this "
            "run's v0 == that design (golden/SEC/FoM relative to it)."
        )
    # The golden trace is the output of the baseline RTL for the random stimulus.
    log("[setup] generating v0 golden trace (RTL sim)...")
    if not harness.generate_golden(d, dirs, log):
        return (None, "golden-generation-failed")
    # A shifted golden trace must fail; otherwise the comparison proves nothing.
    log("[setup] negative control (v0 vs shifted golden must FAIL)...")
    if not harness.negative_control_passes_as_fail(d, dirs, log):
        return (None, "vacuous-verification (negative control did not fail)")
    if sec_plus_tb:
        if d.combinational or not d.resets:
            return (
                None,
                "combinational design cannot use JasperGold SEC (it needs a reset); "
                "re-run this design WITHOUT --sec-plus-tb (the plain RTL+gate testbench "
                "differential is the sound check for combinational logic).",
            )
        log(
            "[setup] --sec-plus-tb: RTL = testbench differential + JasperGold SEC; "
            "gate netlist = testbench differential."
        )
        # The baseline RTL is the fixed reference of every later formal check.
        v0_golden = dirs.golden_dir / f"v0_{dirs.rtl_basenames[0]}"
        shutil.copy2(dirs.primary_rtl, v0_golden)
        dirs.config.write_text(
            harness.gen_config_sh(
                d, dirs.rtl_basenames, sec_golden_file=str(v0_golden.resolve())
            )
        )
        log(
            f"[setup] SEC spec frozen as v0 ({v0_golden.name}); "
            f"SEC_RESET='{harness.sec_reset_expr(d)}'."
        )
    gate_str = "RTL verify + gate verify"
    if sec_plus_tb:
        gate_str += " (+ per-candidate RTL SEC)"
    log(f"[setup] v0 synthesis + {gate_str} ...")
    out = harness.run_flow(dirs, v0_synth_out, skip_verify=False, skip_verify_syn=False)
    harness.harvest_logs(out.work_dir, v0_logs)
    harness.drop_work_dir(out.work_dir)
    if not out.ok:
        return (None, f"v0 flow failed at step '{out.failed_step}'")
    base = read_metrics(Path(out.csv_path))
    if not valid_metrics(base, baseline=True):
        return (None, "baseline area, power, and delay must be finite and positive")
    log(
        f"[setup] v0 OK: area={base['area']:.4f} power={base['mean_power']:.6g} "
        f"timing={base['timing_ns']:.4f} ns  FoM=1.000"
    )
    return (dirs, base)


# ---------------------------------------------------------------------------
# The optimization loop for one design
# ---------------------------------------------------------------------------
def run_one_design(
    d: Design,
    n_iters: int,
    n_vectors: int,
    out_root: Path,
    sec_plus_tb: bool = False,
    adaptive: dict | None = None,
    v0_rtl: str = "",
    start_high: bool = False,
    max_cost: float | None = None,
) -> dict:
    """Measure the baseline, run the iterations, and write the records and report.

    adaptive=None selects fixed effort (EFFORT for every call). Otherwise the
    adaptive policy uses medium effort and switches to a high-effort burst when
    the stall counter reaches the patience value.
    """
    global _LOG_FH, _CALL_COST_CAP
    previous_cost_cap = _CALL_COST_CAP
    _CALL_COST_CAP = max_cost
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = out_root / "runs" / d.key / ts
    run_dir.mkdir(parents=True)
    design_dir = out_root / "designs" / d.key
    _LOG_FH = open(run_dir / "session.log", "w")
    totals = _new_totals()
    td_path = run_dir / "test_data.jsonl"
    td_fh = open(td_path, "w")
    try:

        def record(row: dict) -> None:
            """Append one JSON record to test_data.jsonl."""
            td_fh.write(json.dumps(row) + "\n")
            td_fh.flush()

        def setup_failed(reason: str, **extra) -> dict:
            """Record a setup failure and return the design summary."""
            log(f"[setup] SKIPPING '{d.key}': {reason}")
            record({"design": d.key, "event": "setup_failed", "reason": reason, **extra})
            return {
                "design": d.key,
                "status": "setup_failed",
                "reason": reason,
                "run_dir": str(run_dir),
                "totals": totals,
            }

        log(f"===== design '{d.key}' =====")
        log(
            f"model={MODEL}  iters={n_iters}  vectors={n_vectors}  FoM=ppa  "
            f"flow=DC+PrimeTime ({RESULTS_LABEL})"
        )
        dirs, base = setup_design(
            d,
            design_dir,
            run_dir / "v0" / "synth_out",
            run_dir / "v0" / "synth_logs",
            n_vectors,
            sec_plus_tb=sec_plus_tb,
            v0_rtl=v0_rtl,
        )
        if dirs is None:
            return setup_failed(base)
        flow_proof = _flow_proof(d, run_dir)
        if not flow_proof["ok"]:
            return setup_failed(
                f"all-I/O timing flow proof failed: {flow_proof['why']}",
                provenance=_provenance(flow_proof),
            )
        log(f"[timing] flow proof OK: {flow_proof['summary']}")
        for c in flow_proof["constraints"][:1]:
            for ln in c["lines"]:
                log(f"[timing]   {c['run']}: {ln}")
        if sec_plus_tb:
            verify_label = "RTL testbench + JasperGold SEC; gate testbench"
            verify_report_desc = (
                f"{n_vectors}-vector random differential vs v0 (RTL + gate) + "
                "JasperGold SEC (RTL formal, vs frozen v0), no fix-loop"
            )
        else:
            verify_label = "RTL + gate differential"
            verify_report_desc = (
                f"{n_vectors}-vector random differential vs v0 (RTL + gate), no fix-loop"
            )
        log(f"[setup] equivalence method for '{d.key}': {verify_label}")
        primary = dirs.primary_rtl
        shutil.copytree(dirs.src, run_dir / "v0" / "src", dirs_exist_ok=True)
        base_rtl = primary.read_text()

        # Search state. The best source is the only source that later prompts build on.
        best = {"fom": 1.0, "metrics": base, "rtl": base_rtl, "label": "v0"}
        history = [("v0", "baseline", 1.0, base)]
        accepted = 0
        sid = None  # provider session (Claude) or thread (Codex) ID
        prev = None  # outcome of the previous attempt, for the follow-up prompt
        fresh_session = False  # True after a reply cut at the output limit
        cap_streak = 0
        fail_streak = 0  # attempts in a row without valid measurements

        # Adaptive-effort state: the effort phase, the stall counter, and the burst.
        phase = "medium" if adaptive else None
        stall_n = 0.0
        high_left = 0
        high_cost = 0.0
        total_cost = 0.0
        if adaptive:
            reset = (
                "reset on any accept"
                if adaptive["reset_rho"] <= 0
                else f"soft reset rho={adaptive['reset_rho']:g} on accept"
            )
            log(
                "[adaptive] ON: greedy MEDIUM (revert to best); when the patience count "
                f"reaches {adaptive['patience']} (valid stall +1, non-PPA failure "
                f"+{adaptive['fail_weight']:g}), {reset} -> HIGH burst of up to "
                f"{adaptive['high_iters']} iter(s) (stops on a win), then MEDIUM. "
                f"notes: {_notes_mode()}."
            )
            if start_high:
                phase = "high"
                high_left = adaptive["high_iters"]
                log(
                    "[adaptive] --start-high: iteration 1 begins in a HIGH burst of up "
                    f"to {adaptive['high_iters']} iter(s)."
                )
        record(
            {
                "design": d.key,
                "event": "v0_baseline",
                "iter": 0,
                "metrics": base,
                "fom": 1.0,
                "primary_file": dirs.rtl_basenames[0],
                "rtl": base_rtl,
                "verify_method": "differential+sec" if sec_plus_tb else "differential",
                "notes": _NOTES_META,
                "license_waits": harness.take_license_waits(),
                "provenance": _provenance(flow_proof),
            }
        )
        if LEAN:
            n = _prune_iter_logs(run_dir / "v0")
            if n:
                log(f"[lean] pruned {n} tool-log file(s) from the v0 baseline")
        if n_iters == 0:
            gates = "synth + RTL verify + gate verify"
            if sec_plus_tb:
                gates += " + JasperGold SEC (RTL); v0-vs-v0 SEC skipped (trivial)"
            log(
                f"[check] --iters 0: v0 gate ({gates}) PASSED for '{d.key}' — "
                "synthesis-check mode, no optimization iterations."
            )

        def run_iter(i: int) -> str:
            """Run one iteration and clean its simulator libraries afterwards."""
            try:
                return _run_iter_body(i)
            finally:
                harness.purge_work_libs(run_dir / "iters" / f"i{i:02d}")
                if LEAN:
                    n = _prune_iter_logs(run_dir / "iters" / f"i{i:02d}")
                    if n:
                        log(f"[lean] pruned {n} tool-log file(s) from i{i:02d}")

        def _run_iter_body(i: int) -> str:
            """Ask for one candidate, evaluate it, and update the search state."""
            nonlocal best, sid, prev, accepted, fresh_session, cap_streak, fail_streak
            nonlocal phase, stall_n, high_left, high_cost, total_cost
            cost_before_iteration = totals["cost"]
            iter_effort = phase if adaptive else EFFORT
            adapt_high = bool(adaptive) and phase == "high"
            escape = adapt_high  # a high-effort burst asks for a structural change
            primary.write_text(best["rtl"])
            log(
                f"\n----- iter {i}/{n_iters}  (chaining from {best['label']}, "
                f"FoM={best['fom']:.4f}) -----"
            )

            # 1. Build the prompt: the full prompt for a new session, else a follow-up.
            if i == 1 or fresh_session:
                if fresh_session:
                    log(
                        "[output-limit] the previous call gave no answer; this iteration "
                        "starts a new session with the full prompt and the current best."
                    )
                user_msg = build_prompt(
                    d, best["rtl"], dirs.rtl_basenames[0], best["metrics"], best["fom"]
                )
            else:
                prev_compacted = bool(prev.get("compacted"))
                resend = fail_streak >= RESEND_AFTER_FAILS or prev_compacted
                user_msg = build_followup(
                    prev["outcome"],
                    prev["m"],
                    prev["fom"],
                    best["label"],
                    best["fom"],
                    escape=escape,
                    flow_error=prev.get("flow_error"),
                    best_rtl=best["rtl"] if resend else None,
                    best_primary_file=dirs.rtl_basenames[0],
                    compacted=prev_compacted,
                )
            if adaptive:
                burst = (
                    f"  (HIGH burst {adaptive['high_iters'] - high_left + 1}/"
                    f"{adaptive['high_iters']})"
                    if adapt_high
                    else ""
                )
                log(
                    f"[adaptive] iter {i}: effort={iter_effort}  "
                    f"stall={stall_n:g}/{adaptive['patience']}{burst}"
                )
            # The notes open each new session and follow a context compaction.
            md_live = bool(_NOTES)
            md_sent = bool(
                _NOTES and (sid is None or bool(prev and prev.get("compacted")))
            )
            if md_sent:
                user_msg = _notes_block() + "\n\n" + user_msg

            # 2. Call the model. Ask again when the reply has no code block.
            response, new_sid, call_usage = call_model(
                user_msg,
                run_dir,
                tag=f"i{i:02d}",
                totals=totals,
                resume=sid,
                effort=iter_effort,
                md_sent=md_sent,
            )
            if sid is None:
                sid = new_sid
            thought_m = _THOUGHT_RE.search(response)
            code_str = extract_code(response)
            idea = thought_m.group(1).strip() if thought_m else ""
            log(f"[thought] {idea[:300] or '(none)'}")
            capped = bool(call_usage.get("cap_hit"))
            recode = 0
            while code_str is None and recode < CODE_RETRY_MAX and not capped:
                recode += 1
                log(
                    f"[iter {i}] no ```code``` block — re-prompting for the code "
                    f"({recode}/{CODE_RETRY_MAX})"
                )
                remind = (
                    "Your previous reply did not contain a ```code``` block, so there was "
                    "nothing to evaluate. Resend your COMPLETE optimized module verbatim "
                    "inside a fenced ```code``` block, with your reasoning in a ```thought``` "
                    "block before it. Send the full module (not a diff or snippet); the "
                    "```code``` fence is required for it to be picked up."
                )
                md_sent_r = bool(_NOTES and sid is None)
                if md_sent_r:
                    remind = _notes_block() + "\n\n" + remind
                if max_cost is not None and totals["cost"] >= max_cost:
                    break
                response, new_sid, call_usage = call_model(
                    remind,
                    run_dir,
                    tag=f"i{i:02d}_recode{recode}",
                    totals=totals,
                    resume=sid,
                    effort=iter_effort,
                    md_sent=md_sent_r,
                )
                if sid is None:
                    sid = new_sid
                thought_m = _THOUGHT_RE.search(response)
                code_str = extract_code(response)
                if thought_m:
                    idea = thought_m.group(1).strip()
                log(f"[thought] {idea[:300] or '(none)'}")
                capped = bool(call_usage.get("cap_hit"))

            # 3. Evaluate the candidate: verify, synthesize, measure, and compare.
            iter_dir = run_dir / "iters" / f"i{i:02d}"
            iter_dir.mkdir(parents=True, exist_ok=True)
            cand_code = None
            cand_m = cand_fom = None
            flow_error = None
            sec_pass = None
            if not code_str:
                log(f"[iter] no usable module — discard, revert to {best['label']}.")
                primary.write_text(best["rtl"])
                outcome = (
                    "output-limit, no answer (discarded)"
                    if capped
                    else "no-code (discarded)"
                )
            else:
                cand_code = code_str.strip() + "\n"
                (iter_dir / dirs.rtl_basenames[0]).write_text(cand_code)
                primary.write_text(cand_code)
                out = harness.run_flow(
                    dirs, iter_dir / "synth_out", skip_verify=False, skip_verify_syn=False
                )
                harness.harvest_logs(out.work_dir, iter_dir / "synth_logs")
                harness.drop_work_dir(out.work_dir)
                sec_failed = False
                if out.ok and sec_plus_tb:
                    log(
                        "[iter] testbench PASSED (RTL+gate) — proving RTL equivalence "
                        "with JasperGold SEC..."
                    )
                    sec_out = harness.run_flow(
                        dirs,
                        iter_dir / "sec_out",
                        skip_verify=False,
                        skip_verify_syn=True,
                        use_sec=True,
                        verify_only=True,
                    )
                    harness.harvest_logs(sec_out.work_dir, iter_dir / "sec_logs")
                    harness.drop_work_dir(sec_out.work_dir)
                    sec_failed = not sec_out.ok
                    sec_pass = sec_out.ok
                parsed_metrics = None
                if out.ok and not sec_failed:
                    try:
                        parsed_metrics = read_metrics(Path(out.csv_path))
                    except (OSError, ValueError, RuntimeError):
                        pass
                    if parsed_metrics is None or not valid_metrics(parsed_metrics):
                        log(
                            "[iter] invalid or incomplete area, power, or delay "
                            "measurement; discard."
                        )
                        out = harness.SynthOutcome(
                            False, "metrics", out.csv_path, out.run_id, None
                        )
                if not out.ok or sec_failed:
                    if sec_failed:
                        step = "verify"
                        outcome = "sec-not-equivalent (discarded)"
                        flow_error = extract_flow_error(
                            iter_dir / "sec_logs", step, use_sec=True
                        )
                    else:
                        step = out.failed_step
                        if step == "verify":
                            outcome = "rtl-verify-fail (discarded)"
                        elif step == "metrics":
                            outcome = "invalid-metrics (discarded)"
                        elif step == "verify_syn":
                            outcome = "gate-verify-fail (discarded)"
                        else:
                            outcome = f"synth-error:{step} (discarded)"
                        flow_error = extract_flow_error(iter_dir / "synth_logs", step)
                    if flow_error:
                        log(
                            f"[iter] first flow error ({step}):\n"
                            + "\n".join("    " + l for l in flow_error.splitlines())
                        )
                    log(f"[iter] {outcome} — reverting to {best['label']}.")
                    primary.write_text(best["rtl"])
                else:
                    cand_m = parsed_metrics
                    cand_fom = ppa_fom(cand_m, base)
                    log(
                        f"[iter] candidate: area={cand_m['area']:.4f} "
                        f"power={cand_m['mean_power']:.6g} timing={cand_m['timing_ns']} "
                        f"FoM={cand_fom:.4f}"
                    )
                    if cand_fom < best["fom"]:
                        # A strict improvement becomes the new best source v<N>.
                        prev_best_fom = best["fom"]
                        accepted += 1
                        label = f"v{accepted}"
                        vdir = run_dir / label
                        shutil.copytree(dirs.src, vdir / "src", dirs_exist_ok=True)
                        if Path(out.csv_path).exists():
                            shutil.copy2(out.csv_path, vdir / "output.csv")
                        best = {
                            "fom": cand_fom,
                            "metrics": cand_m,
                            "rtl": cand_code,
                            "label": label,
                        }
                        outcome = "accepted"
                        log(f"[iter] ACCEPTED as {label}: FoM -> {cand_fom:.4f}")
                        if adaptive:
                            # An improvement resets the stall counter and ends a burst.
                            was_high = phase == "high"
                            gain = (
                                (prev_best_fom - cand_fom) / prev_best_fom
                                if prev_best_fom > 0
                                else 0.0
                            )
                            stall_n = _rho_reset(stall_n, gain, adaptive["reset_rho"])
                            phase = "medium"
                            high_left = 0
                            log(
                                f"[adaptive] improvement (gain {gain * 100:.2f}%) -> "
                                f"stall -> {stall_n:.2f}/{adaptive['patience']} "
                                f"(rho={adaptive['reset_rho']:g}), effort {phase.upper()}"
                                + (" (HIGH burst ended on a win)." if was_high else ".")
                            )
                    elif adapt_high:
                        outcome = "high-creative (no improvement)"
                        log(
                            f"[iter] HIGH creative (FoM {cand_fom:.4f} !< "
                            f"{best['fom']:.4f}) — no improvement."
                        )
                    else:
                        primary.write_text(best["rtl"])
                        outcome = "rejected (no improvement)"
                        log(f"[iter] REJECTED (FoM {cand_fom:.4f} !< {best['fom']:.4f}).")

            # 4. Adaptive effort: count stalls, start or end a high-effort burst.
            adapt_high_cost_frac = None
            if adaptive:
                iter_cost = max(0.0, totals["cost"] - cost_before_iteration)
                total_cost += iter_cost
                if adapt_high:
                    high_cost += iter_cost
                adapt_high_cost_frac = high_cost / total_cost if total_cost > 0 else 0.0
                if adapt_high:
                    if outcome != "accepted":
                        high_left -= 1
                        if high_left <= 0:
                            phase = "medium"
                            stall_n = 0
                            primary.write_text(best["rtl"])
                            log(
                                "[adaptive] HIGH burst done, no win -> back to MEDIUM "
                                f"(from best {best['label']})."
                            )
                        else:
                            log(
                                f"[adaptive] HIGH no win -> staying HIGH, {high_left} "
                                "iter(s) left in this burst."
                            )
                elif outcome != "accepted":
                    # A valid candidate adds 1; a failed candidate adds the fail weight.
                    inc = adaptive["fail_weight"] if cand_m is None else 1.0
                    stall_n += inc
                    reason = (
                        f"stall {stall_n:g}/{adaptive['patience']} (patience"
                        + (f", non-PPA failure +{inc:g}" if cand_m is None else "")
                        + ")"
                    )
                    if stall_n >= adaptive["patience"]:
                        log(
                            f"[adaptive] {reason} -> HIGH burst of "
                            f"{adaptive['high_iters']} iter(s)."
                        )
                        phase = "high"
                        high_left = adaptive["high_iters"]
                        stall_n = 0
                    else:
                        log(f"[adaptive] MEDIUM {reason}.")

            # 5. Record the attempt and keep the state for the next prompt.
            fail_streak = fail_streak + 1 if cand_m is None else 0
            history.append(
                (
                    best["label"] if outcome == "accepted" else f"i{i}",
                    outcome,
                    cand_fom,
                    cand_m,
                )
            )
            record(
                {
                    "design": d.key,
                    "event": "iteration",
                    "iter": i,
                    "escape": escape,
                    "session_id": sid,
                    "primary_file": dirs.rtl_basenames[0],
                    "user_message": user_msg,
                    "thought": idea,
                    "code": cand_code,
                    "code_sha256": (
                        hashlib.sha256(cand_code.encode()).hexdigest()
                        if cand_code
                        else None
                    ),
                    "outcome": outcome,
                    "accepted": outcome == "accepted",
                    "metrics": cand_m,
                    "fom": cand_fom,
                    "flow_error": flow_error,
                    "license_waits": harness.take_license_waits(),
                    "verify_method": (
                        "differential+sec" if sec_plus_tb else "differential"
                    ),
                    "sec_pass": sec_pass,
                    "best_label_after": best["label"],
                    "best_fom_after": best["fom"],
                    "effort": iter_effort,
                    "adaptive_phase_next": phase if adaptive else None,
                    "adaptive_stall_n_after": stall_n if adaptive else None,
                    "adaptive_high_left_after": high_left if adaptive else None,
                    "adaptive_high_cost_frac": adapt_high_cost_frac,
                    "md_active": md_live,
                    **({"md_sent": md_sent} if _NOTES else {}),
                    "usage": call_usage,
                    "cumulative_totals": dict(totals),
                }
            )
            prev = {
                "outcome": outcome,
                "m": cand_m,
                "fom": cand_fom,
                "flow_error": flow_error,
                "compacted": bool(call_usage.get("compacted")),
            }
            # A reply cut at the output limit ends the session; the next call starts anew.
            if capped:
                cap_streak += 1
                sid = None
                fresh_session = True
            else:
                cap_streak = 0
                fresh_session = False
            return outcome

        # The iterations stop at the iteration cap, the cost limit, or repeated cut replies.
        for i in range(1, n_iters + 1):
            if max_cost is not None and totals["cost"] >= max_cost:
                log(
                    f"[budget] cumulative cost ${totals['cost']:.4f} >= --max-cost "
                    f"${max_cost:.2f}; stopping before iter {i}/{n_iters} "
                    f"({totals['calls']} call(s) spent)."
                )
                record(
                    {
                        "design": d.key,
                        "event": "cost_budget_stop",
                        "iter": i,
                        "cum_cost": totals["cost"],
                        "max_cost": max_cost,
                        "calls": totals["calls"],
                    }
                )
                break
            run_iter(i)
            if cap_streak >= MAX_CONSECUTIVE_CAP_HITS:
                log(
                    f"[output-limit] {cap_streak} calls in a row ended on the output "
                    "limit with no answer; the run stops (cost so far "
                    f"${totals['cost']:.4f})."
                )
                record(
                    {
                        "design": d.key,
                        "event": "cap_stop",
                        "iter": i,
                        "cap_streak": cap_streak,
                        "cum_cost": totals["cost"],
                    }
                )
                break

        # Summary: the delivered score is never worse than the baseline.
        deliver_fom = min(1.0, best["fom"])
        log(f"\n===== '{d.key}' DONE =====")
        log(
            f"best={best['label']}  deliverable FoM={deliver_fom:.4f}  "
            f"({accepted} improvement(s) over {n_iters} iters)"
        )
        log(
            f"TOTAL: ${totals['cost']:.4f}  in:{totals['in_tok']} out:{totals['out_tok']} "
            f"(thinking~{totals['think_tok_est']})  cache(read:{totals['cache_read']} "
            f"write:{totals['cache_creat']})  {totals['calls']} call(s)"
        )
        record(
            {
                "design": d.key,
                "event": "summary",
                "best_label": best["label"],
                "deliverable_fom": deliver_fom,
                "accepted": accepted,
                "iters": n_iters,
                "totals": totals,
                "notes": _NOTES_META,
            }
        )
        write_report(
            run_dir,
            d,
            n_iters,
            dirs.rtl_basenames[0],
            best,
            deliver_fom,
            history,
            totals,
            verify_desc=verify_report_desc,
        )
        return {
            "design": d.key,
            "status": "ok",
            "best": best["label"],
            "deliverable_fom": deliver_fom,
            "accepted": accepted,
            "run_dir": str(run_dir),
            "test_data": str(td_path),
            "totals": totals,
        }
    finally:
        _CALL_COST_CAP = previous_cost_cap
        td_fh.close()
        if _LOG_FH is not None:
            _LOG_FH.close()
            _LOG_FH = None


# ---------------------------------------------------------------------------
# Report and command line
# ---------------------------------------------------------------------------
def write_report(
    run_dir,
    d,
    n_iters,
    primary_file,
    best,
    deliver_fom,
    history,
    totals,
    verify_desc="Configured equivalence checks",
) -> None:
    """Write report.md: the best source, the cost, and one table row per attempt."""
    basis = (
        "Estimated from user-supplied token prices"
        if PROVIDER == "codex"
        else "Reported by the model CLI"
    )
    lines = [
        f"# ARES run report: {d.key}",
        "",
        f"- Top module: `{d.top}`; optimized file: `{primary_file}`.",
        f"- Model: `{MODEL}`; iteration cap: {n_iters}.",
        f"- Verification: {verify_desc}.",
        "",
        f"**Best verified source: {best['label']}; score: {deliver_fom:.4f}.**",
        "The starting source scores 1.0; lower is better. The score multiplies equally "
        "weighted area, power, and delay ratios relative to that source.",
        f"- Model cost: ${totals['cost']:.4f}. {basis}; this is separate from "
        "subscription billing.",
        f"- Model calls: {totals['calls']}. Input tokens: {totals['in_tok']}; "
        f"output tokens: {totals['out_tok']}.",
        f"- Cached input tokens: {totals['cache_read']}; newly cached tokens: "
        f"{totals['cache_creat']}. Reasoning tokens within the output: approximately "
        f"{totals['think_tok_est']}.",
        "",
        "Each row describes one attempt. A dash means an unavailable or invalid "
        "measurement.",
        "",
        "| Source or attempt | Outcome | Score (lower is better) | Area (library units) "
        "| Mean power (W) | Delay (ns) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for label, outcome, score, metrics in history:
        score_text = (
            f"{score:.4f}"
            if isinstance(score, (int, float)) and math.isfinite(score)
            else "—"
        )
        values = [
            (
                f"{metrics[key]:.6g}"
                if metrics
                and isinstance(metrics.get(key), (int, float))
                and math.isfinite(metrics[key])
                else "—"
            )
            for key in ("area", "mean_power", "timing_ns")
        ]
        lines.append(
            f"| {label} | {outcome} | {score_text} | " + " | ".join(values) + " |"
        )
    (run_dir / "report.md").write_text("\n".join(lines) + "\n")


def argument_parser() -> argparse.ArgumentParser:
    """Return the command-line parser of the optimization loop."""
    ap = argparse.ArgumentParser(
        description=(
            "ARES: optimize RTL with a language model. Every candidate is verified, "
            "synthesized, and measured before it can replace the best source."
        ),
        allow_abbrev=False,
    )
    files = ap.add_argument_group("Inputs and output")
    files.add_argument(
        "--config",
        default=str(HERE / "config" / "designs.json"),
        help="JSON design registry; source paths are relative to this file.",
    )
    selection = files.add_mutually_exclusive_group()
    selection.add_argument("--only", default="", help="Run one registered design key.")
    selection.add_argument(
        "--designs",
        default="",
        help="Comma-separated keys; default: all registered designs.",
    )
    files.add_argument(
        "--out",
        default=str(HERE / "results"),
        help="Output directory; use a distinct directory for each concurrent invocation.",
    )
    files.add_argument(
        "--v0-rtl",
        default="",
        help="Start from this source file; it becomes the baseline (v0) for the "
        "checks and the score.",
    )
    files.add_argument(
        "--list-designs",
        action="store_true",
        help="Validate the registry and list design keys without invoking tools.",
    )
    files.add_argument(
        "--plan",
        action="store_true",
        help="Print resolved settings without invoking tools or making model calls.",
    )
    model = ap.add_argument_group("Model and limits")
    model.add_argument(
        "--provider",
        choices=["claude", "codex"],
        default="claude",
        help="Model CLI; the box_<provider>.sh launcher sets it.",
    )
    model.add_argument(
        "--model",
        default="",
        help="Model ID; defaults to RTL_FLOW_CLAUDE_MODEL or RTL_FLOW_CODEX_MODEL.",
    )
    model.add_argument(
        "--effort",
        choices=EFFORT_LEVELS,
        default=EFFORT,
        help=f"Fixed reasoning effort for every call (default: {EFFORT}).",
    )
    model.add_argument(
        "--max-output-tokens",
        type=int,
        default=MAX_OUTPUT_TOKENS,
        help=f"Claude only: output-token limit per request (default: {MAX_OUTPUT_TOKENS}).",
    )
    model.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(HERE / "private" / "codex_home")),
        help="Isolated Codex home; box_codex.sh sets it.",
    )
    model.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex command; box_codex.sh sets it.",
    )
    model.add_argument(
        "--prices",
        default="",
        help="Codex only: token prices in USD per million tokens (JSON file).",
    )
    model.add_argument(
        "--iters",
        type=int,
        default=None,
        help=f"Iteration cap; default {ITERS_DEFAULT}, or {ITERS_BUDGET_BACKSTOP} with "
        "--max-cost. 0 measures the baseline only.",
    )
    model.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Stop when the model cost reaches this value in USD. The loop checks it "
        "before each call; a call in progress can go past it.",
    )
    verification = ap.add_argument_group("Verification and output files")
    verification.add_argument(
        "--vectors",
        type=int,
        default=harness.N_VECTORS_DEFAULT,
        help=f"Random verification vectors (default: {harness.N_VECTORS_DEFAULT}).",
    )
    verification.add_argument(
        "--sec-plus-tb",
        action="store_true",
        help="Also prove RTL equivalence with JasperGold SEC; the design needs a "
        "clock and a reset.",
    )
    verification.add_argument(
        "--lean",
        action="store_true",
        help="Delete tool logs after each attempt; keep sources, metrics, records, "
        "and the timing proof.",
    )
    notes = ap.add_argument_group("Optimization notes")
    notes.add_argument(
        "--md",
        default="",
        metavar="FILE",
        help="Markdown notes file. ARES sends it in the first user message of each "
        "session, and again after a context compaction.",
    )
    adaptive = ap.add_argument_group(
        "Adaptive effort (defaults: the paper's constants for Claude Opus 4.6 and "
        "for GPT-5.5 with Codex)"
    )
    adaptive.add_argument(
        "--adaptive-effort",
        action="store_true",
        help="Use medium effort, and a high-effort burst when progress stalls.",
    )
    adaptive.add_argument(
        "--adaptive-patience",
        type=int,
        default=None,
        help="Stall count that starts a high-effort burst (default: "
        f"{ADAPT_DEFAULTS['claude']['patience']} for Claude, "
        f"{ADAPT_DEFAULTS['codex']['patience']} for Codex).",
    )
    adaptive.add_argument(
        "--adaptive-fail-weight",
        type=float,
        default=None,
        help="Stall count added by a failed candidate; a valid candidate adds 1 "
        f"(default: {ADAPT_DEFAULTS['claude']['fail_weight']:g} for Claude, "
        f"{ADAPT_DEFAULTS['codex']['fail_weight']:g} for Codex).",
    )
    adaptive.add_argument(
        "--adaptive-reset-rho",
        type=float,
        default=None,
        help="Stall-count reset after an improvement: 0 resets fully, inf never "
        f"resets (default: {ADAPT_DEFAULTS['claude']['reset_rho']:g} for Claude, "
        f"{ADAPT_DEFAULTS['codex']['reset_rho']:g} for Codex).",
    )
    adaptive.add_argument(
        "--adaptive-high-iters",
        type=int,
        default=None,
        help="Maximum iterations in one high-effort burst (default: "
        f"{ADAPT_DEFAULTS['claude']['high_iters']}).",
    )
    adaptive.add_argument(
        "--start-high",
        action="store_true",
        help="Start with a high-effort burst.",
    )
    return ap


def _load_prices(filename: str) -> None:
    """Load the Codex token prices (USD per million tokens) from a JSON file."""
    global _CODEX_PRICE, _CODEX_PRICE_LONG, _CODEX_LONG_CTX
    values = json.loads(Path(filename).read_text())

    def tier(obj):
        names = ("input", "cached_input", "output")
        if not isinstance(obj, dict) or any(name not in obj for name in names):
            raise ValueError(
                "Prices need input, cached_input, and output in USD per million tokens."
            )
        if any(
            type(obj[name]) not in (float, int)
            or not math.isfinite(obj[name])
            or obj[name] < 0
            for name in names
        ):
            raise ValueError("Token prices must be finite and nonnegative.")
        return {
            "in": obj["input"] / 1000000.0,
            "cached": obj["cached_input"] / 1000000.0,
            "out": obj["output"] / 1000000.0,
        }

    _CODEX_PRICE = tier(values)
    _CODEX_PRICE_LONG = (
        tier(values["long_context"]) if "long_context" in values else _CODEX_PRICE
    )
    _CODEX_LONG_CTX = values.get("long_context_threshold", float("inf"))
    if _CODEX_LONG_CTX != float("inf") and (
        type(_CODEX_LONG_CTX) is not int or _CODEX_LONG_CTX < 1
    ):
        raise ValueError("long_context_threshold must be a positive token count.")


def _adaptive_settings(ap: argparse.ArgumentParser, args) -> dict | None:
    """Check the adaptive-effort options and fill in the defaults of the provider."""
    given = {
        "patience": args.adaptive_patience,
        "fail_weight": args.adaptive_fail_weight,
        "reset_rho": args.adaptive_reset_rho,
        "high_iters": args.adaptive_high_iters,
    }
    if not args.adaptive_effort:
        if args.start_high or any(
            value is not None for value in given.values()
        ):
            ap.error("Adaptive settings require --adaptive-effort.")
        return None
    defaults = ADAPT_DEFAULTS[args.provider]
    settings = {
        key: defaults[key] if value is None else value for key, value in given.items()
    }
    if settings["patience"] < 1 or settings["high_iters"] < 1:
        ap.error("--adaptive-patience and --adaptive-high-iters must be at least 1.")
    if not math.isfinite(settings["fail_weight"]) or settings["fail_weight"] < 0:
        ap.error("--adaptive-fail-weight must be finite and nonnegative.")
    if math.isnan(settings["reset_rho"]) or settings["reset_rho"] < 0:
        ap.error("--adaptive-reset-rho must be nonnegative.")
    return settings


def main() -> int:
    """Parse the options, check them, and optimize each selected design in turn."""
    global PROVIDER, MODEL, EFFORT, MAX_OUTPUT_TOKENS, CODEX_HOME, CODEX_BIN, LEAN
    global VECTORS, _NOTES, _NOTES_META
    global _RUN_SETTINGS, _REGISTRY_SHA256
    ap = argument_parser()
    args = ap.parse_args()
    try:
        registry = load_designs(args.config)
        _REGISTRY_SHA256 = _sha256_file(Path(args.config))
    except (OSError, ValueError, TypeError) as exc:
        ap.error(f"Invalid design registry: {exc}")
    if args.only:
        keys = [args.only]
    elif args.designs:
        keys = [key.strip() for key in args.designs.split(",") if key.strip()]
    else:
        keys = list(registry)
    if not keys or any(key not in registry for key in keys):
        ap.error(f"Unknown or empty selection. Registered keys: {', '.join(registry)}")
    if args.list_designs:
        for key in keys:
            print(
                f"{key}: top={registry[key].top}, sources={len(registry[key].src_files)}"
            )
        return 0

    # Check the options.
    if args.iters is None:
        args.iters = (
            ITERS_BUDGET_BACKSTOP if args.max_cost is not None else ITERS_DEFAULT
        )
    if args.iters < 0 or args.vectors < 2 or args.max_output_tokens < 1:
        ap.error(
            "Iterations must be nonnegative, vectors at least 2, and output limit positive."
        )
    if args.max_cost is not None and (
        not math.isfinite(args.max_cost) or args.max_cost <= 0
    ):
        ap.error("--max-cost must be finite and positive.")
    if args.v0_rtl and (len(keys) != 1 or not Path(args.v0_rtl).is_file()):
        ap.error("--v0-rtl requires one selected design and an existing file.")
    adaptive = _adaptive_settings(ap, args)

    # Apply the options to the module settings.
    PROVIDER = args.provider
    MODEL = args.model or (CODEX_MODEL if PROVIDER == "codex" else MODEL)
    EFFORT = args.effort
    MAX_OUTPUT_TOKENS = args.max_output_tokens
    CODEX_HOME = Path(args.codex_home).resolve()
    CODEX_BIN = args.codex_bin
    LEAN = args.lean
    VECTORS = args.vectors
    if args.md:
        try:
            path = Path(args.md).resolve()
            _NOTES = path.read_text()
            _NOTES_META = {
                "path": str(path),
                "sha256": _sha256_file(path),
                "chars": len(_NOTES),
                "mode": _notes_mode(),
            }
        except OSError as exc:
            ap.error(str(exc))
    if args.prices:
        try:
            _load_prices(args.prices)
        except (OSError, ValueError, TypeError) as exc:
            ap.error(f"Invalid prices: {exc}")
    out_root = Path(args.out).resolve()
    _RUN_SETTINGS = {
        "provider": PROVIDER,
        "model": MODEL,
        "effort": EFFORT,
        "vectors": args.vectors,
        "sec_plus_tb": args.sec_plus_tb,
        "adaptive": adaptive,
        "start_high": args.start_high,
        "notes_sha256": (_NOTES_META or {}).get("sha256"),
        "notes_mode": _notes_mode(),
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "prices": _CODEX_PRICE,
        "long_context_prices": _CODEX_PRICE_LONG,
        "long_context_threshold": (
            _CODEX_LONG_CTX if math.isfinite(_CODEX_LONG_CTX) else None
        ),
        "flow_environment": {
            key: value
            for key, value in os.environ.items()
            if key.startswith("RTL_FLOW_")
            and not any(word in key for word in ("TOKEN", "KEY", "AUTH", "PASSWORD"))
        },
    }
    if args.plan:
        print(
            json.dumps(
                {
                    "designs": keys,
                    "output": str(out_root),
                    "iteration_cap": args.iters,
                    "max_cost_usd": args.max_cost,
                    "settings": _RUN_SETTINGS,
                },
                indent=2,
            )
        )
        return 0

    # Model calls must go through the isolated container of a box_<provider>.sh
    # launcher. A baseline measurement (--iters 0) makes no model call.
    box = (os.environ.get("ARES_BOX_ENV") or "").split()
    if args.iters and f"box={PROVIDER}" not in box:
        ap.error(f"Use box_{PROVIDER}.sh to run with an isolated model CLI.")
    if PROVIDER == "codex" and args.iters and _CODEX_PRICE is None:
        ap.error(
            "Codex optimization requires --prices with explicit token prices for cost accounting."
        )
    if PROVIDER == "codex" and args.iters and not (CODEX_HOME / "config.toml").is_file():
        ap.error("Codex launcher must supply an isolated configured home.")
    out_root.mkdir(parents=True, exist_ok=True)
    harness.LOG_FN = log
    summaries = []
    for key in keys:
        try:
            summaries.append(
                run_one_design(
                    registry[key],
                    args.iters,
                    args.vectors,
                    out_root,
                    sec_plus_tb=args.sec_plus_tb,
                    adaptive=adaptive,
                    v0_rtl=args.v0_rtl,
                    start_high=args.start_high,
                    max_cost=args.max_cost,
                )
            )
        except Exception as exc:
            print(f"{key}: {exc}", file=sys.stderr)
            summaries.append({"design": key, "status": "crashed", "error": str(exc)})
    (out_root / "run_summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    print(json.dumps(summaries, indent=2))
    return 0 if all(row["status"] == "ok" for row in summaries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
