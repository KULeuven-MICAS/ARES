# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
# PrimeTime averaged power analysis from the gate-level switching activity (VCD).
set_app_var power_enable_analysis true
set_app_var power_analysis_mode averaged
set link_library [list * {TARGET_LIBRARIES}]
read_verilog {NETLIST}
current_design {DESIGN_NAME}
link_design
{CLOCK_CONSTRAINTS}
{EXTRA_CONSTRAINTS}
read_vcd -strip_path {STRIP_PATH} {VCD_FILE}
update_power
redirect -file {POWER_REPORT} { report_power }
redirect -file {POWER_HIERARCHY_REPORT} { report_power -hierarchy }
exit
