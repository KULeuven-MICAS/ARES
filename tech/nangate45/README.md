# NanGate45 Open Cell Library

ARES uses the NanGate 45 nm Open Cell Library (typical corner: 1.10 V, 25 °C)
as its default technology. All results in the ARES paper use these files.
The library is not manufacturable; it exists for research and for EDA-flow
development.

| File | Content | SHA-256 |
| --- | --- | --- |
| `NangateOpenCellLibrary_typical.lib` | Liberty timing and power models of the 135 cells (typical corner). | `8d540a4d4cf6d09d27c87ad067857a9c0c2eeb023ab7a56e058cd3113db4e9b1` |
| `NangateOpenCellLibrary.v` | Zero-delay functional Verilog models of the same cells, for gate-level simulation. | `5174c5eac34c63cd5c235caf9de76025f0a313a75a73c704853c3c6facff1eb6` |
| `LICENSE` | Apache License 2.0. | |

## Source

- Library: NanGate Open Cell Library, version `PDKv1.3_v2010_12.Apache.CCL`,
  © 2004–2011 Nangate Inc., released under the Apache License 2.0 (see `LICENSE`).
- The Liberty file is the copy distributed in
  [OpenROAD-flow-scripts](https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts)
  under `flow/platforms/nangate45/lib/`. It is unchanged.

## Changes

`NangateOpenCellLibrary.v` is a derived file for this project: a script
generated it from the `function` attributes of the Liberty file. It contains
functional models without timing; the gate-level simulations of ARES check
logic equivalence and record switching activity, and they do not use cell
delays.

The flow compiles the Liberty file into a Synopsys `.db` file with Library
Compiler on the first run and caches the result (see
[docs/configuration.md](../../docs/configuration.md)).
