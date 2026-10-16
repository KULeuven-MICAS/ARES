#!/usr/bin/env bash
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# Run ARES with the Codex CLI. The loop and the hardware tools run on the host;
# every Codex CLI call runs in an Apptainer container (see box_runtime.py).
#   bash box_codex.sh --check                        # test the container and a no-tools request
#   bash box_codex.sh --config FILE --prices FILE [ARES options]
set -euo pipefail
PROJECT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${RTL_FLOW_PYTHON:-python3.11}" "$PROJECT/box_runtime.py" codex "$@"
