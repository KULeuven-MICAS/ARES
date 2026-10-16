#!/usr/bin/env bash
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# Flow entry point: RTL verification by JasperGold sequential equivalence (--sec-plus-tb).
set -eu
export RTL_FLOW_VERIFY_MODE=sec
exec "${RTL_FLOW_PYTHON:-python3.11}" "$(dirname "$0")/runner.py" "$@"
