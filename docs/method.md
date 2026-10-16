# Method

This page describes what ARES does in one run. The numbers are the defaults.

## 1. Baseline

1. ARES copies the sources into a work folder and generates four SystemVerilog
   testbenches from the registry: a golden-trace testbench, an equivalence
   testbench, a negative-control testbench, and a power testbench.
2. The golden-trace testbench simulates the original source (v0) with seeded
   random inputs and records all outputs in each cycle (the golden trace).
   Resets occur at the start and at random later cycles.
3. The negative control compares v0 with the golden trace shifted by one cycle.
   This comparison must fail. If it passes, the outputs do not change enough to
   detect a wrong design, and ARES stops the run for this design.
4. The flow measures v0 (see section 3). v0 has a score of 1.0.
5. ARES checks that the synthesis script of v0 constrains the clock and all
   input and output paths, and keeps that script in `v0/flow_proof/`.

## 2. Iterations

Each iteration asks the model for one candidate and evaluates it.

- **Prompt.** The first call of a session gets the goal, the hard constraints,
  the current best source, and its measurements. Later calls continue the same
  session with a follow-up prompt: the result of the previous candidate and the
  next instruction.
- **Best source.** Before each iteration, ARES restores the best source, so each
  candidate is a change of the best source. After two failed candidates in a
  row, or after an automatic context compaction, the follow-up prompt includes
  the best source again.
- **Error feedback.** When a tool step fails, the follow-up prompt includes the
  first error line of the tool log, with one line of context.
- **Reply format.** The model replies with a `thought` block and a `code` block.
  When the code block is missing, ARES asks again, at most two times.
- **Output limit.** A reply that stops at the output-token limit counts as a
  failed attempt. The next iteration starts a new session. Three such replies in
  a row stop the run.

## 3. Evaluation of a candidate

A candidate must pass all checks in this order. A failure discards it.

1. **RTL equivalence.** The equivalence testbench simulates the candidate with
   the same random inputs and compares each output bit with the golden trace.
   Bits that are unknown in the golden trace are not compared.
2. **Synthesis.** Design Compiler (`compile_ultra`) synthesizes the candidate
   for a 0.1 ns clock, with zero input and output delays, so that the reported
   delay is the critical path that synthesis reaches. A combinational design
   gets a virtual clock.
3. **Gate equivalence.** The equivalence testbench also runs on the gate-level
   netlist (zero-delay functional models).
4. **Power.** A gate-level simulation writes the switching activity of 4,000
   random cycles after 16 warm-up cycles to a VCD file. The clock period of this
   simulation is the measured delay times 1.10. PrimeTime computes the averaged
   power from this activity.
5. **Formal equivalence (optional).** With `--sec-plus-tb`, JasperGold proves
   sequential equivalence of the candidate RTL and the v0 RTL after all other
   steps pass.

The figure of merit is

```text
FoM = (area / area_v0) × (power / power_v0) × (delay / delay_v0)
```

A candidate becomes the new best source only when its FoM is strictly lower
than the FoM of the best source. Missing or invalid measurements never win.

## 4. Reasoning effort

With `--effort LEVEL`, every call uses the same effort.

With `--adaptive-effort`, the effort follows a stall counter *s*:

```text
phase = medium (or high with --start-high)
for each iteration:
    call the model with the effort of the phase
    if the candidate improves the FoM by a relative gain g:
        s = s × max(0, 1 − g / κ)        (κ = 0: s = 0)
        phase = medium                  (an improvement ends a burst)
    else if phase == high:
        one burst iteration is used; after the last one: phase = medium, s = 0
    else:
        s = s + 1   for a valid candidate
        s = s + w   for a failed candidate
        if s ≥ p:  phase = high for at most n iterations, s = 0
```

The constants are the patience *p*, the fail weight *w*, the reset scale *κ*,
and the burst length *n*. The defaults depend on the provider and are the
constants of the paper: *p* = 3, *w* = 2.8, *κ* = 0.05, *n* = 1 for Claude
(Opus 4.6), and *p* = 4, *w* = 4, *κ* = 0.1, *n* = 1 for Codex (GPT-5.5).

A high-effort iteration gets the "escape" prompt. It asks the model to change
or revert one structural decision and to explore a different family of
designs. `--start-high` starts the run with a burst.

## 5. Optimization notes

`--md FILE` adds a Markdown file with optimization directions. The prompt
calls them directions that lowered the FoM on other designs, and asks the model
to test them against the current design.

The notes open the first user message of each session and stay in the session
history; the system prompt does not hold them. After a context compaction,
ARES sends them again.

## 6. Cost

- **Claude.** The CLI reports the cost of each call. The CLI version is pinned,
  because the cost report of other versions can differ.
- **Codex.** ARES computes the cost from the token counts and the price file.

`--max-cost` stops the run before a call when the total cost reaches the limit.
A call in progress can go past the limit.

## 7. Isolation of the model

The model has no tools. Each call runs in an Apptainer container that sees only
the current run folder, a fresh provider home, the login data, and the CLI. The
Claude call denies all tools and stops the run when the CLI loads a tool or the
reply uses one. Before a Codex run, ARES captures one request locally and checks
that it declares no tools; during the run, any tool event stops the run.

## 8. Records

`test_data.jsonl` holds one JSON record per line:

| Event | Content |
| --- | --- |
| `v0_baseline` | Baseline source and measurements, settings, and code checksums. |
| `iteration` | Prompt, thought, candidate source, outcome, measurements, FoM, effort, adaptive state, and token usage. |
| `cost_budget_stop` | The cost limit stopped the run. |
| `cap_stop` | Three replies in a row stopped at the output limit. |
| `setup_failed` | The baseline failed; the record gives the reason. |
| `summary` | Best source, delivered FoM, and total usage. |

The delivered FoM is the FoM of the best source, never more than 1.0.
