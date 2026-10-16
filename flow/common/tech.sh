#!/usr/bin/env bash
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# Technology files and tool commands of the flow.
# The values come from the RTL_FLOW_* environment settings (see docs/configuration.md).
# synthesis.py sets the bundled NanGate45 library when no library is configured.
# Lists use ':' as a separator; entries may be absolute or relative to TECH_DIR.
TECH_DIR=${RTL_FLOW_TECH_DIR:-}
IFS=: read -r -a SYNTH_LIBS <<< "${RTL_FLOW_SYNTH_LIBS:-}"
IFS=: read -r -a SIM_MODELS <<< "${RTL_FLOW_SIM_MODELS:-}"
IFS=: read -r -a SYNTHETIC_LIBS <<< "${RTL_FLOW_SYNTHETIC_LIBS:-}"

# Tool commands: a name on the PATH or an absolute path.
DC_SHELL=${RTL_FLOW_DC_SHELL:-dc_shell}
LC_SHELL=${RTL_FLOW_LC_SHELL:-lc_shell}
PT_SHELL=${RTL_FLOW_PT_SHELL:-pt_shell}
VLIB=${RTL_FLOW_VLIB:-vlib}
VLOG=${RTL_FLOW_VLOG:-vlog}
VSIM=${RTL_FLOW_VSIM:-vsim}
JASPERGOLD=${RTL_FLOW_JASPERGOLD:-jaspergold}
