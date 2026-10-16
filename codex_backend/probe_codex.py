#!/usr/bin/env python3
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Inspect a Codex request through a local capture server, with no model call.

The CLI receives a temporary provider pointing at localhost. The server logs
the request body and returns an error; it never forwards the request upstream.
Both top-level tools and tools in additional_tools input items are checked.
Validation applies to the captured request; it does not test a live response.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tomllib

HERE = Path(__file__).resolve().parent


def all_tools(body: dict) -> list[str]:
    """Return the names of all tools that a request declares."""
    names = [
        tool.get("name") or tool.get("type") or "unknown"
        for tool in body.get("tools") or []
    ]
    for item in body.get("input") or []:
        if item.get("type") != "additional_tools":
            continue
        for namespace in item.get("tools") or []:
            prefix = namespace.get("name") or "unknown"
            for tool in namespace.get("tools") or []:
                names.append(
                    f"{prefix}.{tool.get('name') or tool.get('type') or 'unknown'}"
                )
    return names


def configured_model(codex_home: Path) -> str | None:
    """Return the model that a Codex home configures."""
    with (codex_home / "config.toml").open("rb") as source:
        return tomllib.load(source).get("model")


def capture_request(
    codex_home: Path,
    work: Path,
    port: int,
    effort: str,
    prompt: str,
    codex_bin: str,
    stock: bool = False,
    model: str | None = None,
) -> dict:
    """Return a request captured locally; port 0 selects a free local port."""
    out = work / ("capture_stock.jsonl" if stock else "capture.jsonl")
    out.write_text("")
    server = subprocess.Popen(
        [sys.executable, str(HERE / "capture_server.py"), str(out), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready, _, _ = select.select([server.stdout], [], [], 10)
        line = server.stdout.readline().strip() if ready else ""
        if not line.startswith("READY "):
            raise RuntimeError("local capture server failed to start")
        actual_port = int(line.split()[1])
        provider = (
            'model_providers.probe={name="probe",'
            f'base_url="http://127.0.0.1:{actual_port}/v1",'
            'wire_api="responses",env_key="PROBE_KEY",'
            "requires_openai_auth=false,request_max_retries=0,"
            "stream_max_retries=0}"
        )
        command = [
            codex_bin,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-C",
            str(work),
            "-c",
            'model_provider="probe"',
            "-c",
            provider,
            "-c",
            f'model_reasoning_effort="{effort}"',
        ]
        if model:
            command += ["-m", model]
        if stock:
            command.append("--ignore-user-config")
        command.append(prompt)
        environment = dict(os.environ, CODEX_HOME=str(codex_home), PROBE_KEY="probe")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            env=environment,
            cwd=work,
        )
        (work / "capture_cli.stderr").write_text(result.stderr)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    for line in out.read_text().splitlines():
        record = json.loads(line)
        if record.get("body") and record.get("path", "").endswith("/responses"):
            body = json.loads(record["body"])
            if "input" in body:
                return body
    raise RuntimeError(
        f"no model request captured; inspect {work / 'capture_cli.stderr'}"
    )


def validate_request(body: dict, prompt_path: Path) -> list[str]:
    """Return the problems of a captured request: tools, extra context, or a wrong prompt."""
    errors = []
    tools = all_tools(body)
    if tools:
        errors.append(f"request declares tools: {tools}")
    wanted = prompt_path.read_text().strip()
    if (body.get("instructions") or "").strip() != wanted:
        errors.append("request instructions differ from the configured system prompt")
    inputs = body.get("input") or []
    if [item.get("role") for item in inputs] != ["user"]:
        errors.append("request input includes unexpected messages or context")
    serialized = json.dumps(inputs)
    for marker in (
        "<environment_context>",
        "<skills_instructions>",
        "<permissions instructions>",
        "AGENTS.md",
    ):
        if marker in serialized:
            errors.append(f"request contains unexpected context marker {marker}")
    return errors


def main() -> int:
    """Capture one request locally and print the result of the audit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-home", required=True, help="generated clean configuration directory"
    )
    parser.add_argument(
        "--work", required=True, help="directory for local request capture files"
    )
    parser.add_argument("--model", help="override the template model")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--codex-bin", default="codex")
    args = parser.parse_args()
    home = Path(args.codex_home).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    if not (home / "config.toml").is_file():
        parser.error("--codex-home has no config.toml; run make_codex_env.py first")
    body = capture_request(
        home,
        work,
        args.port,
        args.effort,
        "LOCAL_REQUEST_CHECK",
        args.codex_bin,
        model=args.model or configured_model(home),
    )
    (work / "request_body.json").write_text(json.dumps(body, indent=2) + "\n")
    errors = validate_request(body, home / "system_prompt.txt")
    for error in errors:
        print(f"[probe] FAIL: {error}")
    if not errors:
        print(
            "[probe] PASS: local request has no tools or extra context; no model call was made"
        )
    return int(bool(errors))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"[FAIL] {error}") from None
