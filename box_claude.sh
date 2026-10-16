#!/usr/bin/env bash
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# Run ARES with Claude Code. The loop and the hardware tools run on the host;
# every Claude CLI call runs in an Apptainer container (see box_runtime.py).
#   bash box_claude.sh --check                       # test the container, no model call
#   bash box_claude.sh --config FILE [ARES options]  # optimize the registered designs
set -euo pipefail
PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${RTL_FLOW_PYTHON:-python3.11}" "$PROJECT/box_runtime.py" claude "$@"
