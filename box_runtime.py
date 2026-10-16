#!/usr/bin/env python3
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""Launcher runtime of box_claude.sh and box_codex.sh.

The optimization loop and the hardware tools run on the host. Every model CLI
call runs in an Apptainer container that sees only the run directory, a fresh
provider home, the login data, and the CLI itself. The launcher builds that
container command as a small "shim" script, puts the shim first on the PATH,
and then starts optimize.py.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT / "codex_backend"))

# CLI pin for the flow's per-call cost accounting. Check the cost reporting,
# including resumed sessions, when you change this version.
CLAUDE_VERSION = "2.1.181"
CLAUDE_SHA256 = "35ffd4e9d9a86395d0ba4e05f8b23bf098bfeab95e97deacd6537909d1324e9c"
# Claude Code settings inside the container: no updates, fallbacks, memory, or skills.
CLAUDE_ENV = {
    "DISABLE_AUTOUPDATER": "1",
    "CLAUDE_CODE_NO_MODEL_FALLBACK": "1",
    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_ORG_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_POLICY_SKILLS": "1",
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
    "CLAUDE_CODE_DISABLE_WORKFLOWS": "1",
    "CLAUDE_CODE_DISABLE_FAST_MODE": "1",
}

# The shim replaces the provider CLI on the PATH. It runs the real CLI in the
# container, with the current run directory as the only writable host path.
# Login secrets go into the container environment at call time and are never
# written into the shim.
SHIM = '''#!{python}
"""Run the provider CLI in Apptainer with the run directory and the configured paths."""
import json, os, pathlib, sys
c = json.loads({config!r})
cwd = pathlib.Path.cwd().resolve()
root = pathlib.Path(c["allowed_root"])
if not cwd.is_relative_to(root):
    sys.exit("[FAIL] provider working directory is outside the configured output root")
if any(character in str(cwd) for character in (":", ",", "\\n")):
    sys.exit("[FAIL] provider working directory contains unsupported bind-path characters")
env = {{k: v for k, v in os.environ.items()
       if not k.startswith(("APPTAINERENV_", "SINGULARITYENV_"))}}
if c.get("token_file"):
    token = pathlib.Path(c["token_file"]).read_text().strip()
    if not token:
        sys.exit("[FAIL] CLAUDE_OAUTH_TOKEN_FILE is empty")
    env["APPTAINERENV_CLAUDE_CODE_OAUTH_TOKEN"] = token
elif c.get("api_key_env"):
    key = os.environ.get(c["api_key_env"], "")
    if not key:
        sys.exit("[FAIL] " + c["api_key_env"] + " is empty")
    env["APPTAINERENV_ANTHROPIC_API_KEY"] = key
args = [c["apptainer"], "exec", "--cleanenv", "--containall", "--no-mount", "hostfs"]
if c.get("home"):
    args += ["--home", c["home"][0] + ":" + c["home"][1]]
for key, value in c["env"].items():
    args += ["--env", key + "=" + value]
for source, destination, mode in c["binds"]:
    args += ["--bind", source + ":" + destination + (":" + mode if mode else "")]
args += ["--bind", str(cwd) + ":" + str(cwd), "--pwd", str(cwd), c["image"], c["binary"]]
os.execvpe(args[0], args + sys.argv[1:], env)
'''


def fail(message: str) -> None:
    """Stop the launcher with a [FAIL] message."""
    raise SystemExit(f"[FAIL] {message}")


def external_setting(name: str) -> Path:
    """Return the path in setting `name`; it must be outside the source directory."""
    value = os.environ.get(name)
    if not value:
        fail(f"set {name} to an existing path outside the source directory")
    path = Path(value).expanduser().resolve()
    if path.is_relative_to(PROJECT):
        fail(f"{name} must be outside the source directory")
    return path


def executable(value: str, label: str) -> Path:
    """Resolve a command name or path to an executable file."""
    path = shutil.which(value)
    if not path:
        fail(f"{label} is unavailable: {value}")
    return Path(path).resolve()


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a short helper command and capture its output."""
    return subprocess.run(command, text=True, capture_output=True, timeout=60, **kwargs)


def version(binary: Path, required: str | None) -> str:
    """Return the CLI version text; stop when it is not the required version."""
    result = run([str(binary), "--version"])
    if result.returncode:
        fail(f"CLI --version failed for {binary}: {result.stderr.strip()}")
    text = result.stdout.strip()
    if required and required not in text.split():
        fail(f"CLI reports {text!r}; expected version {required!r}")
    return text


def help_text(provider: str) -> str:
    """Return the launcher help text."""
    common = f"""Usage: box_{provider}.sh [--check] [flow options]

The optimization loop and the hardware tools run on the host. Each model CLI
call runs in Apptainer; it sees only the run directory, a fresh provider home,
the login data, and the CLI. --check tests the container and the CLI startup
without a model call.

Common settings:
  RTL_FLOW_SIF         Container image: a .sif file or an unpacked image folder.
  RTL_FLOW_PYTHON      Host Python 3.11+ executable (default: python3.11).
  APPTAINER_BIN        Container runtime executable (default: apptainer).
"""
    if provider == "claude":
        return (
            common
            + f"""
Claude settings:
  CLAUDE_BIN           Installed standalone Claude CLI executable.
  CLAUDE_VERSION       Expected CLI version (default: {CLAUDE_VERSION}).
  CLAUDE_SHA256        Expected binary checksum (default: the pinned release).
Claude login (the first one that is set applies):
  CLAUDE_OAUTH_TOKEN_FILE  File with a token from `claude setup-token`.
  ANTHROPIC_API_KEY    Anthropic API key.
  CLAUDE_AUTH_HOME     .claude directory with .credentials.json from `claude login`.

When you change the CLI version, also set its published checksum and check
that its cost reports stay per call, also for resumed sessions.
"""
        )
    return (
        common
        + """
Codex settings:
  CODEX_AUTH_HOME      External directory containing the shared auth.json.
  CODEX_TEMPLATE_HOME  External clean template made by make_codex_env.py.
  CODEX_BIN            Installed CLI executable (default: codex on PATH).
  CODEX_PREFIX         Optional directory containing the CLI and its runtime.
  CODEX_VERSION        Optional expected CLI version.

The launcher supplies --provider, --codex-home and --codex-bin to the flow.
Template sessions, caches, plugins and other state are never copied.
"""
    )


def make_shim(path: Path, config: dict) -> None:
    """Write the shim; it keeps arguments as arrays, also paths with spaces."""
    path.write_text(SHIM.format(python=sys.executable, config=json.dumps(config)))
    path.chmod(0o700)


def check_box(config: dict, box: Path, provider: str, cli_version: str) -> int:
    """Check access to visible and hidden test files with the configured binds."""
    work = box / "check_work"
    work.mkdir()
    config = dict(config, allowed_root=str(work))
    visible = work / "visible_canary.txt"
    private = box / "private_canary.txt"
    secret = hashlib.sha256(os.urandom(32)).hexdigest()
    visible.write_text(secret)
    private.write_text(secret)
    failed = False

    def probe(program: str, *args: str) -> subprocess.CompletedProcess:
        probe_path = box / "probe"
        make_shim(
            probe_path, dict(config, binary=program, token_file=None, api_key_env=None)
        )
        return run([sys.executable, str(probe_path), *args], cwd=work)

    readable = probe("/bin/cat", str(visible))
    ok = readable.returncode == 0 and readable.stdout == secret
    print(f"[check] visible control: {'PASS' if ok else 'FAIL'}")
    failed |= not ok
    write = probe("/bin/touch", str(work / "write_canary"))
    ok = write.returncode == 0 and (work / "write_canary").exists()
    print(f"[check] run directory writable: {'PASS' if ok else 'FAIL'}")
    failed |= not ok

    # A failed container startup cannot pass: the positive controls above must
    # succeed, and this command must run successfully inside the same box.
    for hidden in [private, PROJECT / "optimize.py"]:
        result = probe(
            "/bin/sh", "-c", 'if test -r "$1"; then exit 1; fi', "check", str(hidden)
        )
        ok = result.returncode == 0
        print(f"[check] hidden {hidden.name}: {'PASS' if ok else 'FAIL'}")
        failed |= not ok
    result = probe(config["binary"], "--version")
    ok = result.returncode == 0 and result.stdout.strip() == cli_version
    print(f"[check] boxed CLI version: {'PASS' if ok else 'FAIL'}")
    failed |= not ok
    if failed:
        marker = PROJECT / ".apptainer" / f"CHECK_OK_{provider}"
        marker.unlink(missing_ok=True)
        print("[check] confinement or CLI startup failed; check runtime configuration")
        return 1
    marker = PROJECT / ".apptainer" / f"CHECK_OK_{provider}"
    if provider == "claude":
        marker.write_text(f"offline confinement check; {cli_version}\n")
    print("[check] PASS: confinement and CLI startup; no model call was made")
    return 0


def claude_login(
    binds: list, container_home: str, state: Path, output: Path
) -> tuple[str | None, str | None]:
    """Select the Claude login: a setup-token file, an API key, or .credentials.json.

    Return (token_file, api_key_env) for the shim. A credentials file is copied
    into the fresh home and bound back to its source, so refreshed tokens persist.
    """
    if os.environ.get("CLAUDE_OAUTH_TOKEN_FILE"):
        token_file = external_setting("CLAUDE_OAUTH_TOKEN_FILE")
        if not token_file.is_file():
            fail("CLAUDE_OAUTH_TOKEN_FILE is missing")
        if token_file.is_relative_to(output):
            fail("the output root must not contain CLAUDE_OAUTH_TOKEN_FILE")
        return str(token_file), None
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None, "ANTHROPIC_API_KEY"
    auth = external_setting("CLAUDE_AUTH_HOME")
    auth_file = auth / ".credentials.json"
    if auth.is_relative_to(output):
        fail("the output root must not contain the authentication source")
    if not auth_file.is_file():
        fail(
            f"no Claude login: set CLAUDE_OAUTH_TOKEN_FILE or ANTHROPIC_API_KEY, or sign "
            f"in with `claude login` so that {auth_file} exists"
        )
    clean = state / ".claude"
    clean.mkdir(exist_ok=True)
    shutil.copyfile(auth_file, clean / ".credentials.json")
    (clean / ".credentials.json").chmod(0o600)
    binds.append([str(auth_file), f"{container_home}/.claude/.credentials.json", ""])
    return None, None


def main() -> int:
    """Check the settings, build the container shim, and start optimize.py."""
    if len(sys.argv) < 2 or sys.argv[1] not in ("claude", "codex"):
        fail("box_runtime.py requires provider claude or codex")
    provider, args = sys.argv[1], sys.argv[2:]
    if "--help" in args or "-h" in args:
        print(help_text(provider), flush=True)
        return subprocess.call([sys.executable, str(PROJECT / "optimize.py"), "--help"])
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--out")
    parser.add_argument("--codex-home")
    parser.add_argument("--codex-bin")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--effort")
    known, _ = parser.parse_known_args(args)
    if known.check:
        (PROJECT / ".apptainer" / f"CHECK_OK_{provider}").unlink(missing_ok=True)
    if known.provider and known.provider != provider:
        fail(f"box_{provider}.sh requires --provider {provider}")
    if known.codex_bin or known.codex_home:
        fail(
            "configure CODEX_BIN and CODEX_TEMPLATE_HOME; the launcher supplies the "
            "flow's Codex paths"
        )

    # 1. Container image, output root, and provider CLI.
    image_value = os.environ.get("RTL_FLOW_SIF")
    if not image_value:
        fail("set RTL_FLOW_SIF to your Apptainer container image")
    # The image is a .sif file, or an unpacked image folder for systems without FUSE.
    image = Path(image_value).expanduser().resolve()
    if not image.exists():
        fail(f"container image is missing: {image}")
    apptainer = executable(os.environ.get("APPTAINER_BIN", "apptainer"), "Apptainer")
    output = Path(known.out).expanduser().resolve() if known.out else PROJECT / "results"
    if PROJECT.is_relative_to(output):
        fail("the output root must not contain the flow source directory")
    if any(character in str(output) for character in (":", ",", "\n")):
        fail("the output root contains unsupported Apptainer bind-path characters")

    required = os.environ.get(f"{provider.upper()}_VERSION")
    if provider == "claude":
        binary_value = os.environ.get("CLAUDE_BIN")
        if not binary_value:
            fail("set CLAUDE_BIN to the standalone Claude CLI executable")
        binary = executable(binary_value, "Claude CLI")
        required = required or CLAUDE_VERSION
        checksum = os.environ.get("CLAUDE_SHA256")
        if not checksum and required != CLAUDE_VERSION:
            fail("a different CLAUDE_VERSION also requires CLAUDE_SHA256")
        checksum = checksum or CLAUDE_SHA256
        if hashlib.sha256(binary.read_bytes()).hexdigest() != checksum.lower():
            fail("Claude CLI binary does not match CLAUDE_SHA256")
        prefix = None
    else:
        auth = external_setting("CODEX_AUTH_HOME")
        auth_file = auth / "auth.json"
        if not auth_file.is_file():
            fail(f"authentication file is missing: {auth_file}; sign in with codex login first")
        if auth.is_relative_to(output):
            fail("the output root must not contain the authentication source")
        binary = executable(os.environ.get("CODEX_BIN", "codex"), "Codex CLI")
        configured_prefix = os.environ.get("CODEX_PREFIX")
        if configured_prefix:
            prefix = Path(configured_prefix).expanduser().resolve()
        else:
            prefix = next(
                (p for p in binary.parents if p.name == "node_modules"), binary.parent
            )
        if not prefix.is_dir() or not binary.is_relative_to(prefix):
            fail(
                "CODEX_PREFIX must be a directory containing the resolved CLI executable"
            )
        if PROJECT.is_relative_to(prefix) or auth_file.is_relative_to(prefix):
            fail(
                "CODEX_PREFIX must not include the source directory or authentication source"
            )
    cli_version = version(binary, required)

    # 2. A fresh provider home for this output root; a lock stops a second launch.
    appdir = PROJECT / ".apptainer"
    tag = hashlib.sha256(str(output).encode()).hexdigest()[:20]
    if known.check:
        tag = f"check_{os.getpid()}"
    box = appdir / f"{provider}_boxes" / tag
    box.mkdir(parents=True, exist_ok=True)
    box.chmod(0o700)
    lockdir = appdir / "output_locks"
    lockdir.mkdir(exist_ok=True)
    lockdir.chmod(0o700)
    lock_fd = os.open(str(lockdir / f"{tag}.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        fail("another process is using this output root; choose a different --out")
    os.set_inheritable(lock_fd, True)
    state = box / "home"
    if state.exists():
        shutil.rmtree(state)
    state.mkdir(mode=0o700)
    metadata = {"output_root": str(output), "provider": provider, "cli_version": cli_version}

    # 3. Container binds and environment.
    # Apptainer does not allow HOME in --env; --home mounts the fresh provider home
    # and sets HOME inside the container.
    container_home = "/home/ares"
    home = None
    binds = [["/usr", "/usr", "ro"]]
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    token_file = api_key_env = None
    if provider == "claude":
        home = [str(state), container_home]
        token_file, api_key_env = claude_login(binds, container_home, state, output)
        # Minimal onboarding state, so that the CLI starts without questions.
        (state / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding": True}))
        binds.append([str(binary), str(binary), "ro"])
        env.update(CLAUDE_ENV)
    else:
        template = external_setting("CODEX_TEMPLATE_HOME")
        for name in ("config.toml", "catalog_clean.json", "system_prompt.txt"):
            source = template / name
            if not source.is_file():
                fail(f"template file is missing: {source}; run make_codex_env.py first")
            shutil.copyfile(source, state / name)
        config = (state / "config.toml").read_text()
        config = config.replace(
            json.dumps(str(template))[1:-1], json.dumps(str(state))[1:-1]
        )
        (state / "config.toml").write_text(config)
        shutil.copyfile(auth_file, state / "auth.json")
        (state / "auth.json").chmod(0o600)
        binds += [
            [str(state), str(state), ""],
            [str(auth_file), str(state / "auth.json"), ""],
            [str(prefix), str(prefix), "ro"],
        ]
        env["CODEX_HOME"] = str(state)
        env["PATH"] = f"{prefix}/bin:/usr/local/bin:/usr/bin:/bin"

    for source, destination, _ in binds + ([home + [""]] if home else []):
        if any(c in source + destination for c in (":", ",", "\n")):
            fail("Apptainer bind paths cannot contain colons, commas, or newlines")
    config = {
        "home": home,
        "apptainer": str(apptainer),
        "image": str(image),
        "binary": str(binary),
        "allowed_root": str(output),
        "env": env,
        "binds": binds,
        "token_file": token_file,
        "api_key_env": api_key_env,
    }
    shimdir = box / "bin"
    shimdir.mkdir(exist_ok=True)
    make_shim(shimdir / provider, config)
    if known.check:
        checked = check_box(config, box, provider, cli_version)
        if checked or provider == "claude":
            return checked

    # 4. Codex only: capture one local request and check that it declares no tools.
    selected_model = known.model
    selected_effort = known.effort
    if provider == "codex":
        import tomllib
        from probe_codex import capture_request, validate_request

        with (state / "config.toml").open("rb") as source:
            template_config = tomllib.load(source)
        selected_model = selected_model or template_config.get("model")
        selected_effort = selected_effort or template_config.get(
            "model_reasoning_effort", "medium"
        )
        if not selected_model:
            fail("the template needs a model setting, or supply --model")
        metadata.update(model=selected_model, effort=selected_effort)
        work = (
            box / "check_work"
            if known.check
            else output / ".provider_check" / str(os.getpid())
        )
        work.mkdir(parents=True, exist_ok=True)
        request_shim = box / "request_probe"
        make_shim(request_shim, dict(config, allowed_root=str(work)))
        body = capture_request(
            state,
            work,
            0,
            selected_effort,
            "LOCAL_REQUEST_CHECK",
            str(request_shim),
            model=selected_model,
        )
        (work / "request_body.json").write_text(json.dumps(body, indent=2) + "\n")
        errors = validate_request(body, state / "system_prompt.txt")
        if errors:
            (appdir / "CHECK_OK_codex").unlink(missing_ok=True)
            fail("offline request audit failed: " + "; ".join(errors))
        print(
            "[check] Codex request has no tools or extra context; local capture only",
            flush=True,
        )
        if known.check:
            (appdir / "CHECK_OK_codex").write_text(
                f"offline confinement and no-tools request checks; {cli_version}\n"
            )
            return 0
    (box / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    # 5. Start the optimization loop on the host with the shim first on the PATH.
    flow_args = args + ["--provider", provider, "--out", str(output)]
    if provider == "codex":
        flow_args += [
            "--codex-home",
            str(state),
            "--codex-bin",
            str(shimdir / provider),
            "--model",
            selected_model,
            "--effort",
            selected_effort,
        ]
        os.environ["CODEX_HOME"] = str(state)
    else:
        os.environ["ARES_CLAUDE_HOME"] = str(state / ".claude")
    os.environ["PATH"] = str(shimdir) + os.pathsep + os.environ.get("PATH", "")
    os.environ["ARES_BOX_ENV"] = (
        f"box={provider} cli={cli_version.replace(' ', '_')} "
        "confined=containall+no-mount-hostfs fresh_home=1"
    )
    print(f"[run] provider={provider}; CLI={cli_version}; output={output}", flush=True)
    os.chdir(PROJECT)
    os.execv(sys.executable, [sys.executable, str(PROJECT / "optimize.py"), *flow_args])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"[FAIL] {error}") from None
