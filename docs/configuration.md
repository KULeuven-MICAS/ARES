# Configuration

ARES has three kinds of settings: the design registry (a JSON file), the
command-line options of `optimize.py`, and environment settings for the tools.
`config/settings.example.sh` is a template for the environment settings.

## Design registry

The registry is a JSON object with one key, `designs`, that holds a list of
designs. Source paths are relative to the JSON file.

| Field | Type | Meaning |
| --- | --- | --- |
| `key` | string | Short name of the design. ARES uses it for the result folders. |
| `top` | string | Name of the top module. |
| `src_files` | list of paths | Source files. ARES optimizes the first file and copies the others unchanged. |
| `lang` | `"v"` or `"sv"` | Language of the optimized file. |
| `clocks` | list of ports | Clock ports. An empty list marks a combinational design. |
| `resets` | list of `[port, "high" or "low"]` | Reset ports with their active level. |
| `inputs` | list of `[port, width]` | Data inputs. The testbench drives random values on them. |
| `outputs` | list of `[port, width]` | Outputs. The checks compare them with the baseline. |
| `extract_top` | boolean | `true`: take only the top module from the first file. |
| `clock_gating` | boolean | `true`: let Design Compiler insert clock gating. |
| `pt_hold` | list of `[input, value]` | Inputs that hold a fixed value during the power simulation. The equivalence checks still use random values. |

Rules:

- List each port once. Clock and reset ports are not in `inputs`.
- Widths are bit counts. A value in `pt_hold` must fit the width of its input.
- `--sec-plus-tb` needs a clock and a reset.
- Several registered clocks toggle together with the same period. The
  testbench does not model separate clock domains.
- The registry does not read parameters from the source. For a parameterized
  module, register the port widths of the configuration that you want.

## Command-line options

`python3.11 optimize.py --help` lists all options. The launchers
`box_claude.sh` and `box_codex.sh` accept the same options.

| Option | Default | Meaning |
| --- | --- | --- |
| `--config FILE` | `config/designs.json` | Design registry. |
| `--only KEY`, `--designs K1,K2` | all designs | Select designs. ARES runs them one after the other. |
| `--out DIR` | `results` | Output folder. Use a different folder for each run at the same time. |
| `--v0-rtl FILE` | none | Start from this source. It becomes the baseline for the checks and the score. |
| `--list-designs`, `--plan` | | Check the registry and the settings; no tool and no model call. |
| `--provider` | `claude` | `claude` or `codex`. The launcher sets it. |
| `--model ID` | `claude-opus-4-6` or `gpt-5.5` | Model ID. |
| `--effort LEVEL` | `high` | Fixed reasoning effort: `low`, `medium`, or `high`. |
| `--max-output-tokens N` | 128000 | Claude only: output-token limit per request. |
| `--iters N` | 25 (100 with `--max-cost`) | Iteration limit. `0` measures the baseline only. |
| `--max-cost USD` | none | Cost limit. ARES checks it before each call; a call in progress can go past it. |
| `--prices FILE` | none | Codex only: token prices (see below). Required for Codex runs. |
| `--vectors N` | 10000 | Random verification vectors (cycles for a clocked design). |
| `--sec-plus-tb` | off | Also prove RTL equivalence with JasperGold. |
| `--lean` | off | Delete the tool logs of each attempt; keep sources, measurements, and records. |
| `--md FILE` | none | Optimization notes, sent in the first message of each session (see [method.md](method.md)). |
| `--adaptive-effort` | off | Use the adaptive policy (see [method.md](method.md)). |
| `--adaptive-patience P` | 3 (Claude), 4 (Codex) | Stall count that starts a high-effort burst. |
| `--adaptive-fail-weight W` | 2.8 (Claude), 4 (Codex) | Stall count of a failed candidate (a valid candidate adds 1). |
| `--adaptive-reset-rho K` | 0.05 (Claude), 0.1 (Codex) | Counter reset after an improvement; 0 resets fully. |
| `--adaptive-high-iters N` | 1 | Maximum length of a high-effort burst. |
| `--start-high` | off | Start with a high-effort burst. |

## Measurement settings

Set these environment variables before a run. All names start with `RTL_FLOW_`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `RTL_FLOW_FREQUENCY_MHZ` | 10000 | Synthesis clock target. 10,000 MHz is a 0.1 ns period, so the reported delay is the critical path that synthesis reaches. |
| `RTL_FLOW_CLK_UNCERTAINTY_SETUP_NS`, `..._HOLD_NS` | 0 | Clock uncertainty in ns. |
| `RTL_FLOW_RETIME` | false | Let synthesis move registers. |
| `RTL_FLOW_NO_AUTOUNGROUP` | false | Keep the hierarchy. |
| `RTL_FLOW_CLOCK_GATING` | the design's `clock_gating` | Clock gating in synthesis. |
| `RTL_FLOW_POWER_PERIOD_MODE` | critical_path | Clock period of the power simulation: the measured delay times the margin, or `target` for the target period. |
| `RTL_FLOW_POWER_PERIOD_MARGIN` | 1.10 | Margin on the measured delay, at least 1. |
| `RTL_FLOW_VECTORS` | 10000 | Default of `--vectors`. |
| `RTL_FLOW_POWER_VECTORS` | 4000 | Cycles (or input vectors) of the power simulation. |
| `RTL_FLOW_POWER_WARMUP` | 16 | Cycles after reset before the switching activity is recorded. |
| `RTL_FLOW_RESET_CYCLES` | 8 | Cycles of the initial reset. |
| `RTL_FLOW_RESET_ASSERT_DIV` | 32 | The testbench asserts a reset in 1 of this many cycles; 0 turns this off. |
| `RTL_FLOW_SETTLE_NS` | 2.0 | Settle time per input vector of a combinational design, in ns. |
| `RTL_FLOW_SEED` | 0xC0DE0042 | Seed of the random stimulus. |
| `RTL_FLOW_LICENSE_WAIT_S` | 300 | Wait time before ARES runs a step again after a license failure or a full disk. |
| `RTL_FLOW_LICENSE_MAX_WAIT_S` | 43200 | Maximum total wait time (12 hours). |

## Tools and technology

| Setting | Default | Meaning |
| --- | --- | --- |
| `RTL_FLOW_PYTHON` | python3.11 | Python command of the launchers and the flow. |
| `RTL_FLOW_DC_SHELL` | dc_shell | Design Compiler command. |
| `RTL_FLOW_LC_SHELL` | lc_shell | Library Compiler command. |
| `RTL_FLOW_PT_SHELL` | pt_shell | PrimeTime command. |
| `RTL_FLOW_VLIB`, `RTL_FLOW_VLOG`, `RTL_FLOW_VSIM` | vlib, vlog, vsim | QuestaSim commands. |
| `RTL_FLOW_JASPERGOLD` | jaspergold | JasperGold command. |
| `RTL_FLOW_TECH_DIR` | `tech/nangate45` | Folder for relative technology paths. |
| `RTL_FLOW_SYNTH_LIBS` | `NangateOpenCellLibrary_typical.lib` | Synthesis libraries (`.lib` or `.db`), separated by `:`. |
| `RTL_FLOW_SIM_MODELS` | `NangateOpenCellLibrary.v` | Verilog cell models for gate-level simulation, separated by `:`. |
| `RTL_FLOW_SYNTHETIC_LIBS` | empty | Optional synthetic (DesignWare) libraries. |
| `RTL_FLOW_TECH_CACHE` | `.work/technology_cache` | Cache of the compiled `.db` files. |
| `RTL_FLOW_WORK_BASE` | `.work/run` | Prefix of the work folders; each flow run adds an ID. |
| `RTL_FLOW_RESULTS_LABEL` | `Synopsys Design Compiler + Nangate 45nm` | Name of the measurement setup in the prompt. Change it when you use another library. |

The NanGate45 default applies only when `RTL_FLOW_TECH_DIR`,
`RTL_FLOW_SYNTH_LIBS`, and `RTL_FLOW_SIM_MODELS` are all unset. Library Compiler
compiles each `.lib` file once; the cache key is the content of the file.

## Launchers

[providers.md](providers.md) describes the provider settings: the container
image, the CLI paths, and the login data.

## Codex prices

Codex reports tokens but no cost. ARES computes the cost from a price file in
US dollars per million tokens. `config/prices.gpt-5.5.json` holds the list
prices of GPT-5.5 that the paper used (read on 2026-09-08):

```json
{
  "input": 5.0,
  "cached_input": 0.5,
  "output": 30.0,
  "long_context_threshold": 272000,
  "long_context": {"input": 10.0, "cached_input": 1.0, "output": 45.0}
}
```

The long-context prices apply to a call with more input tokens than the
threshold. The prices are an estimate; a subscription bills in a different way.
