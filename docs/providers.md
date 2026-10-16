# Model providers

The optimization loop and the hardware tools run on the host. Each model call
runs in an Apptainer container that sees only the current run folder, a fresh
provider home, the login data, and the CLI. `box_claude.sh` and `box_codex.sh`
set up this container and then start `optimize.py`.

## Container image

The container only needs a small Linux system; the launcher mounts the host
`/usr` read-only. The paper used a Rocky Linux 8 image.

```bash
export RTL_FLOW_SIF="$HOME/ares/rockylinux8.sif"
apptainer pull "$RTL_FLOW_SIF" \
    docker://rockylinux@sha256:9794037624aaa6212aeada1d28861ef5e0a935adaf93e4ef79837119f2a2d04c
```

The launcher starts the container with `--containall --no-mount hostfs`, so
the host file system is not visible, also on sites where the Apptainer
configuration mounts it by default. The fresh provider home is the container
home (`--home`), so the session history stays on the host between calls.

On a system without FUSE, `apptainer exec` cannot mount a `.sif` file. Unpack
the image into a folder and set `RTL_FLOW_SIF` to that folder:

```bash
apptainer build --sandbox "$HOME/ares/rockylinux8" "$HOME/ares/rockylinux8.sif"
export RTL_FLOW_SIF="$HOME/ares/rockylinux8"
```

## Claude Code

1. Install the pinned CLI. ARES pins version 2.1.181 because its cost report
   per call is known. `fetch_cli.sh` downloads the official release and checks
   its SHA-256 checksum.

   ```bash
   export CLAUDE_BIN="$HOME/ares/claude-2.1.181/claude"
   bash fetch_cli.sh
   ```

2. Select one login method. The launcher uses the first one that is set.

   | Setting | Login |
   | --- | --- |
   | `CLAUDE_OAUTH_TOKEN_FILE` | A file with a long-lived token from `claude setup-token` (subscription). |
   | `ANTHROPIC_API_KEY` | An Anthropic API key. |
   | `CLAUDE_AUTH_HOME` | A `.claude` folder with `.credentials.json` from `claude login`. |

   Keep the login files outside the repository and outside the output folder.
   The launcher passes a token or a key into the container at call time; it
   never writes them into a file.

3. Check the container and the CLI. This check makes no model call.

   ```bash
   bash box_claude.sh --check
   ```

4. Run ARES.

   ```bash
   bash box_claude.sh --config examples/designs.json --iters 5 --out results/claude-test
   ```

To use another CLI version, set `CLAUDE_VERSION` and its `CLAUDE_SHA256`. Then
check that the CLI reports the cost of each call, also for resumed sessions.

## Codex CLI

1. Install the Codex CLI (the paper used version 0.149.0) and log in with
   `codex login`. Set `CODEX_BIN` when the CLI is not on the `PATH`, and
   `CODEX_AUTH_HOME` to the folder with `auth.json`.

2. Make a clean template home. It disables every tool, skill, and feature, and
   it copies no login data. Make it again after every CLI update.

   ```bash
   export CODEX_TEMPLATE_HOME="$HOME/ares/codex-template"
   python3.11 codex_backend/make_codex_env.py \
       --out "$CODEX_TEMPLATE_HOME" --model gpt-5.5 --effort medium
   ```

3. Check the container and a local request. The check sends one request to a
   local capture server, not to a model, and rejects any declared tool.

   ```bash
   bash box_codex.sh --check
   ```

4. Run ARES with a price file (see [configuration.md](configuration.md)). The
   adaptive defaults for Codex are the GPT-5.5 constants of the paper.

   ```bash
   bash box_codex.sh --config examples/designs.json \
       --prices config/prices.gpt-5.5.json --iters 5 --out results/codex-test
   ```

`CODEX_PREFIX` can name the folder of the CLI and its runtime, and
`CODEX_VERSION` can require a CLI version.

## Common rules

- Each output folder has its own provider home, and a lock prevents two runs
  in the same output folder. Use a different `--out` for each run at the same
  time.
- Each launch starts with a fresh provider home; no state of an earlier run is
  used.
- A provider usage limit makes ARES wait and try again every hour.
