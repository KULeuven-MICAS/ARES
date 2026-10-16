# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# Design Compiler synthesis script; runner.py fills in the placeholders for one run.
set target_library [list {TARGET_LIBRARIES}]
set synthetic_library [list {SYNTHETIC_LIBRARIES}]
set link_library [list * {TARGET_LIBRARIES} {SYNTHETIC_LIBRARIES}]
{INCLUDE_DIRECTORIES}
{READ_RTL}
{ELABORATE}
current_design {DESIGN_NAME}

# Zero input and output delays include paths that cross the module boundary.
{CLOCK_CONSTRAINTS}
{EXTRA_CONSTRAINTS}
compile_ultra {COMPILE_FLAGS}

report_area > {AREA_REPORT}
report_timing -max_paths 1 -significant_digits 5 > {TIMING_REPORT}
report_qor -significant_digits 5 > {QOR_REPORT}
write -format verilog -hierarchy -output {NETLIST}
exit
