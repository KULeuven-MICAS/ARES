# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Provider checks using synthetic files and mocks; no model or EDA calls."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import contextlib
import io
import unittest
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
BACKEND = PROJECT / "codex_backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(PROJECT))
import box_runtime
import codex_call
import make_codex_env
import probe_codex

repair_spec = importlib.util.spec_from_file_location(
    "public_repair", PROJECT / "repair_session.py"
)
repair_module = importlib.util.module_from_spec(repair_spec)
repair_spec.loader.exec_module(repair_module)


class ExecRequested(Exception):
    pass


class ProviderTests(unittest.TestCase):
    def test_catalog_braces_inside_strings_are_preserved(self):
        catalog = {
            "models": [
                {
                    "slug": "synthetic-model",
                    "instructions": 'Use {x} and "quoted }" text.',
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "cli"
            binary.write_bytes(
                b"prefix" + json.dumps(catalog, indent=2).encode() + b"\xffsuffix"
            )
            extracted = make_codex_env.extract_catalog(binary)
            self.assertEqual(extracted, catalog)
            cleaned = make_codex_env.strip_tools(extracted)
            self.assertIsNone(cleaned["models"][0]["apply_patch_tool_type"])

    def test_request_audit_rejects_tools_in_both_channels(self):
        top = {"tools": [{"type": "web_search"}]}
        additional = {
            "input": [
                {
                    "type": "additional_tools",
                    "tools": [{"name": "functions", "tools": [{"name": "exec"}]}],
                }
            ]
        }
        self.assertEqual(probe_codex.all_tools(top), ["web_search"])
        self.assertEqual(probe_codex.all_tools(additional), ["functions.exec"])

    def test_event_audit_rejects_a_tool_that_only_started(self):
        record = codex_call.parse_events(
            [
                json.dumps({"type": "thread.started", "thread_id": "synthetic-thread"}),
                json.dumps(
                    {"type": "item.started", "item": {"type": "command_execution"}}
                ),
            ]
        )
        self.assertEqual(record["thread_id"], "synthetic-thread")
        self.assertEqual(record["tool_items"], ["command_execution"])

    def test_resume_command_and_usage_accounting(self):
        command = codex_call.build_cmd(
            "next", resume="synthetic-thread", cwd=Path("/tmp/work")
        )
        self.assertEqual(command[:4], ["codex", "exec", "resume", "synthetic-thread"])
        self.assertNotIn("-C", command)
        usage = {"input_tokens": 120, "cached_input_tokens": 40, "output_tokens": 12}
        result = codex_call.parse_events(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "reply"},
                    }
                ),
                json.dumps({"type": "turn.completed", "usage": usage}),
            ]
        )
        self.assertEqual(result["usage"], usage)
        self.assertEqual(result["text"], "reply")

    def test_repair_preserves_text_and_relinks_ancestors(self):
        rows = [
            {"uuid": "a", "message": {"role": "user", "content": "summary"}},
            {
                "uuid": "b",
                "parentUuid": "a",
                "message": {"role": "assistant", "content": [{"type": "thinking"}]},
            },
            {
                "uuid": "c",
                "parentUuid": "b",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "redacted_thinking"},
                        {"type": "text", "text": "keep"},
                    ],
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            source, dest = (
                Path(directory) / "source.jsonl",
                Path(directory) / "dest.jsonl",
            )
            source.write_text("\n".join(json.dumps(row) for row in rows))
            counts = repair_module.repair(str(source), str(dest))
            repaired = [json.loads(line) for line in dest.read_text().splitlines()]
            self.assertEqual(counts["nodes_dropped"], 1)
            self.assertEqual(counts["blocks_stripped"], 2)
            self.assertEqual(repaired[-1]["parentUuid"], "a")
            self.assertEqual(
                repaired[-1]["message"]["content"], [{"type": "text", "text": "keep"}]
            )
            self.assertEqual(dest.stat().st_mode & 0o777, 0o600)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "optimize.py").write_text("# synthetic flow entrypoint\n")
        self.binary = self.root / "bin" / "cli"
        self.binary.parent.mkdir()
        self.binary.write_text("#!/bin/sh\nexit 0\n")
        self.binary.chmod(0o700)
        self.image = self.root / "image.sif"
        self.image.touch()
        self.auth = self.root / "auth"
        self.auth.mkdir()
        (self.auth / ".credentials.json").write_text("{}")
        (self.auth / "auth.json").write_text("{}")
        self.template = self.root / "template"
        self.template.mkdir()
        (self.template / "system_prompt.txt").write_text("Generic prompt.")
        (self.template / "catalog_clean.json").write_text('{"models": []}')
        make_codex_env.write_config(
            self.template,
            "synthetic-model",
            "medium",
            self.template / "catalog_clean.json",
            self.template / "system_prompt.txt",
        )
        (self.template / "sessions").mkdir()
        (self.template / "sessions" / "unrelated.txt").write_text("unrelated state")
        self.output = self.project / "results"
        self.fds = []
        self.old_cwd = Path.cwd()
        self.env = {
            "RTL_FLOW_SIF": str(self.image),
            "APPTAINER_BIN": str(self.binary),
            "CLAUDE_BIN": str(self.binary),
            "CLAUDE_VERSION": "1.0",
            "CLAUDE_SHA256": hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            "CLAUDE_AUTH_HOME": str(self.auth),
            "CODEX_BIN": str(self.binary),
            "CODEX_AUTH_HOME": str(self.auth),
            "CODEX_TEMPLATE_HOME": str(self.template),
        }

    def tearDown(self):
        os.chdir(self.old_cwd)
        for descriptor in self.fds:
            os.close(descriptor)
        self.temp.cleanup()

    def launch(self, provider, *args, keep_lock=False):
        calls = {}

        def execute(binary, argv):
            calls["argv"] = argv
            calls["home"] = Path(
                os.environ["ARES_CLAUDE_HOME"]
                if provider == "claude"
                else os.environ["CODEX_HOME"]
            )
            raise ExecRequested()

        def capture(home, work, port, effort, prompt, cli, **kwargs):
            calls["audit_model"] = kwargs["model"]
            calls["audit_effort"] = effort
            return {
                "tools": [],
                "instructions": "Generic prompt.",
                "input": [{"role": "user"}],
            }

        previous_fds = len(self.fds)
        with contextlib.redirect_stdout(io.StringIO()), patch.dict(
            os.environ, self.env, clear=True
        ), patch.object(box_runtime, "PROJECT", self.project), patch.object(
            sys, "argv", ["box_runtime.py", provider, "--out", str(self.output), *args]
        ), patch.object(
            box_runtime,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "fake 1.0\n", ""),
        ), patch.object(
            os,
            "set_inheritable",
            side_effect=lambda descriptor, value: self.fds.append(descriptor),
        ), patch.object(
            os, "execv", side_effect=execute
        ), patch.object(
            probe_codex, "capture_request", side_effect=capture
        ):
            with self.assertRaises(ExecRequested):
                box_runtime.main()
        if not keep_lock:
            for descriptor in self.fds[previous_fds:]:
                os.close(descriptor)
            del self.fds[previous_fds:]
        os.chdir(self.old_cwd)
        return calls

    def test_every_launch_starts_with_a_fresh_provider_home(self):
        for provider in ("claude", "codex"):
            home = self.launch(provider)["home"]
            self.assertFalse((home / "sessions" / "unrelated.txt").exists())
            sentinel = home / "session_marker"
            sentinel.write_text("state of the previous run")
            self.launch(provider)
            self.assertFalse(sentinel.exists())

    def test_resume_run_option_is_not_a_launcher_option(self):
        calls = self.launch("claude", "--resume-run", "somewhere")
        # The launcher passes it on; the flow's parser then rejects it.
        self.assertIn("--resume-run", calls["argv"])

    def test_codex_audit_and_flow_use_same_model_and_effort(self):
        calls = self.launch(
            "codex", "--model", "other-synthetic-model", "--effort", "high"
        )
        self.assertEqual(calls["audit_model"], "other-synthetic-model")
        self.assertEqual(calls["audit_effort"], "high")
        self.assertEqual(
            calls["argv"][-4:],
            ["--model", "other-synthetic-model", "--effort", "high"],
        )

    def test_output_lock_is_shared_between_providers(self):
        self.launch("claude", keep_lock=True)
        with patch.dict(os.environ, self.env, clear=True), patch.object(
            box_runtime, "PROJECT", self.project
        ), patch.object(
            sys, "argv", ["box_runtime.py", "codex", "--out", str(self.output)]
        ), patch.object(
            box_runtime,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "fake 1.0\n", ""),
        ):
            with self.assertRaisesRegex(
                SystemExit, "another process is using this output root"
            ):
                box_runtime.main()

    def test_claude_login_order_token_then_api_key_then_credentials(self):
        state = self.root / "state"
        state.mkdir()
        token = self.root / "token"
        token.write_text("synthetic-token\n")
        cases = (
            ({"CLAUDE_OAUTH_TOKEN_FILE": str(token), "ANTHROPIC_API_KEY": "k"}, (str(token), None), 0),
            ({"ANTHROPIC_API_KEY": "k", "CLAUDE_AUTH_HOME": str(self.auth)}, (None, "ANTHROPIC_API_KEY"), 0),
            ({"CLAUDE_AUTH_HOME": str(self.auth)}, (None, None), 1),
        )
        for env, expected, credential_binds in cases:
            with self.subTest(env=sorted(env)), patch.dict(os.environ, env, clear=True), patch.object(
                box_runtime, "PROJECT", self.project
            ):
                binds = []
                self.assertEqual(
                    box_runtime.claude_login(binds, "/home/x", state, self.output),
                    expected,
                )
                self.assertEqual(len(binds), credential_binds)
        empty = self.root / "empty"
        empty.mkdir()
        with patch.dict(os.environ, {"CLAUDE_AUTH_HOME": str(empty)}, clear=True), patch.object(
            box_runtime, "PROJECT", self.project
        ):
            with self.assertRaisesRegex(SystemExit, "no Claude login"):
                box_runtime.claude_login([], "/home/x", state, self.output)

    def test_api_key_reaches_the_container_but_not_the_shim_file(self):
        self.output.mkdir(parents=True)
        apptainer = self.root / "fake-apptainer"
        apptainer.write_text('#!/bin/sh\nprintf "%s" "$APPTAINERENV_ANTHROPIC_API_KEY"\n')
        apptainer.chmod(0o700)
        shim = self.root / "shim"
        box_runtime.make_shim(
            shim,
            {
                "allowed_root": str(self.output),
                "apptainer": str(apptainer),
                "image": str(self.image),
                "binary": str(self.binary),
                "env": {},
                "binds": [],
                "token_file": None,
                "api_key_env": "ANTHROPIC_API_KEY",
            },
        )
        result = subprocess.run(
            [sys.executable, str(shim)],
            cwd=self.output,
            capture_output=True,
            text=True,
            env={"PATH": os.environ.get("PATH", ""), "ANTHROPIC_API_KEY": "synthetic-key"},
        )
        self.assertEqual(result.stdout, "synthetic-key")
        self.assertNotIn("synthetic-key", shim.read_text())

    def test_generated_shim_refuses_a_working_directory_outside_output(self):
        shim = self.root / "shim"
        box_runtime.make_shim(shim, {"allowed_root": str(self.output)})
        compile(shim.read_text(), str(shim), "exec")
        result = subprocess.run(
            [sys.executable, str(shim)], cwd=self.root, capture_output=True, text=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside the configured output root", result.stderr)

    def test_box_check_does_not_accept_a_failed_container(self):
        box = self.project / "box"
        box.mkdir()
        config = {
            "allowed_root": str(self.output),
            "binary": str(self.binary),
            "apptainer": str(self.binary),
            "image": str(self.image),
            "env": {},
            "binds": [],
            "token_file": None,
        }
        with contextlib.redirect_stdout(io.StringIO()), patch.object(
            box_runtime, "PROJECT", self.project
        ), patch.object(
            box_runtime,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "", "failed to start"),
        ):
            self.assertEqual(
                box_runtime.check_box(config, box, "claude", "fake 1.0"), 1
            )
        self.assertFalse((self.project / ".apptainer" / "CHECK_OK_claude").exists())


if __name__ == "__main__":
    unittest.main()
