#!/usr/bin/env bash
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# ARES environment settings.
#
# Copy this file to a place outside the repository, edit the paths, and load it
# in every shell that runs ARES:
#
#   cp config/settings.example.sh ~/ares-settings.sh
#   source ~/ares-settings.sh
#
# The versions in the comments are the versions that the ARES paper used.
# docs/configuration.md describes every setting.

# ---------------------------------------------------------------------------
# 1. Python 3.11 or newer (paper: 3.11.13). The flow uses the standard library only.
# ---------------------------------------------------------------------------
export RTL_FLOW_PYTHON=python3.11

# ---------------------------------------------------------------------------
# 2. EDA tools. Put each tool on the PATH (for example with your site's setup
#    script or `module load`), or set the absolute executable path below.
# ---------------------------------------------------------------------------
# Synopsys Design Compiler (paper: U-2022.12-SP2), synthesis.
#   export PATH="/opt/synopsys/syn/U-2022.12-SP2/bin:$PATH"
export RTL_FLOW_DC_SHELL=dc_shell
# Synopsys Library Compiler (paper: U-2022.12-SP2), compiles the .lib file once.
#   export PATH="/opt/synopsys/lc/U-2022.12-SP2/bin:$PATH"
export RTL_FLOW_LC_SHELL=lc_shell
# Synopsys PrimeTime (paper: U-2022.12-SP2), power analysis.
#   export PATH="/opt/synopsys/prime/U-2022.12-SP2/bin:$PATH"
export RTL_FLOW_PT_SHELL=pt_shell
# Siemens QuestaSim (paper: 2022.4), RTL and gate-level simulation.
#   export PATH="/opt/questasim/2022.4/linux_x86_64:$PATH"
export RTL_FLOW_VLIB=vlib
export RTL_FLOW_VLOG=vlog
export RTL_FLOW_VSIM=vsim
# Cadence JasperGold (paper: 2025.09), only for --sec-plus-tb.
#   export PATH="/opt/cadence/jasper/2025.09/bin:$PATH"
export RTL_FLOW_JASPERGOLD=jaspergold
# License servers of the tools (port@host), as your site requires:
#   export SNPSLMD_LICENSE_FILE=27000@license-server
#   export MGLS_LICENSE_FILE=27001@license-server
#   export CDS_LIC_FILE=27002@license-server

# ---------------------------------------------------------------------------
# 3. Technology. Leave these three settings empty to use the bundled NanGate45
#    library in tech/nangate45 (the paper's library).
# ---------------------------------------------------------------------------
# export RTL_FLOW_TECH_DIR=/path/to/technology
# export RTL_FLOW_SYNTH_LIBS=cells.lib          # ':' between several files
# export RTL_FLOW_SIM_MODELS=cells.v            # ':' between several files
# export RTL_FLOW_RESULTS_LABEL="Synopsys Design Compiler + <your library>"

# ---------------------------------------------------------------------------
# 4. Apptainer (paper: 1.5) and the container image for the model CLI calls.
#    Make the image once (see README.md):
#      apptainer pull ~/ares/rockylinux8.sif docker://rockylinux@sha256:9794037624aaa6212aeada1d28861ef5e0a935adaf93e4ef79837119f2a2d04c
# ---------------------------------------------------------------------------
export APPTAINER_BIN=apptainer
export RTL_FLOW_SIF="$HOME/ares/rockylinux8.sif"

# ---------------------------------------------------------------------------
# 5. Claude Code (paper: CLI 2.1.181 with Claude Opus 4.6).
#    Install the pinned CLI once:  CLAUDE_BIN=... bash fetch_cli.sh
#    Login: set ONE of the three settings below.
# ---------------------------------------------------------------------------
export CLAUDE_BIN="$HOME/ares/claude-2.1.181/claude"
export RTL_FLOW_CLAUDE_MODEL=claude-opus-4-6
# export CLAUDE_OAUTH_TOKEN_FILE="$HOME/.config/ares/claude-token"  # from `claude setup-token`
# export ANTHROPIC_API_KEY=...                                      # an API key
export CLAUDE_AUTH_HOME="$HOME/.claude"                             # after `claude login`

# ---------------------------------------------------------------------------
# 6. Codex CLI (paper: 0.149.0 with GPT-5.5). Only for box_codex.sh.
# ---------------------------------------------------------------------------
export CODEX_BIN=codex
export RTL_FLOW_CODEX_MODEL=gpt-5.5
export CODEX_AUTH_HOME="$HOME/.codex"                               # after `codex login`
export CODEX_TEMPLATE_HOME="$HOME/ares/codex-template"              # made by make_codex_env.py
