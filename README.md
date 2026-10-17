# analog-sizing-agent

An LLM agent that sizes a **two-stage Miller-compensated OTA** with **ngspice in the loop**. Claude proposes transistor sizes from hand analysis, runs a simulation through a tool, reads the gain, bandwidth, phase margin, power and operating points, and adjusts until the spec is met or the simulation budget runs out.

```
spec ──► Claude (gm/Id reasoning) ──► simulate_ota(sizes) ──► ngspice
              ▲                                                  │
              └──── metrics + operating points + pass/fail ◄─────┘
```

The agent loop is written by hand in `sizing_agent/agent.py`, without a framework, so every step is visible.

Two modes:

- **`--mode equations` (default): equation-first methodology.** The agent chooses design knobs; hand-analysis equations (`sizing_agent/equations.py`) compute the sizes; the simulator only accepts designs that came from the equations; mismatches are fixed by back-annotating process parameters from the simulated operating points or by changing knobs. See [the methodology](#equation-first-methodology).
- **`--mode free`:** the agent picks transistor sizes directly.

Two processes: `--process generic` (built-in BSIM4 cards, runs with any ngspice) and `--process sg13g2` (IHP open PDK, see [examples/sg13g2](examples/sg13g2/README.md)).

## Quick start

```bash
git clone https://github.com/kouchev/analog-sizing-agent
cd analog-sizing-agent
./scripts/bootstrap.sh            # .venv + SG13G2 toolchain in .tools/ (Linux; first run 15-20 min)
source .venv/bin/activate         # also sets PDK_ROOT and NGSPICE

python -m sizing_agent --process sg13g2 --check   # scripted loop, no API key needed
export ANTHROPIC_API_KEY=...                      # from console.anthropic.com
python -m sizing_agent --process sg13g2           # the agent
```

`bootstrap.sh` creates `.venv` with the package and pytest, downloads the IHP SG13G2 models, builds OpenVAF-Reloaded and ngspice 45 into `.tools/` (git-ignored), compiles the PSP103 transistor model, and finishes by running the tests. Running it again skips everything that is already built. Without activating the venv, the code still finds `.tools/` on its own.

Only the generic models, no SG13G2 build: `./scripts/bootstrap.sh --python` (needs `ngspice` on PATH, e.g. `sudo apt install ngspice`).

The build needs Ubuntu/Debian (it installs LLVM 18, autotools and bison with apt). On macOS or another distribution, install those yourself first.

In Claude Code cloud sessions, `.claude/hooks/session-start.sh` runs the bootstrap automatically when a session starts.

Every run writes `runs/<timestamp>/log.jsonl` (each tool call and result) and `runs/<timestamp>/summary.md` (best design, trajectory table and the agent's final answer).

### Options

```bash
python -m sizing_agent --gain-db 65 --gbw-mhz 80 --pm-deg 60 --power-mw 0.8 --cl-pf 2 \
                       --max-sims 25 --effort high
```

| Option | Default | Meaning |
|---|---|---|
| `--gain-db` | 60 | Minimum DC gain |
| `--gbw-mhz` | 50 | Minimum unity-gain bandwidth |
| `--pm-deg` | 60 | Minimum phase margin |
| `--power-mw` | 1.0 | Maximum power |
| `--cl-pf` | 2.0 | Load capacitance |
| `--mode` | equations | `equations` (methodology) or `free` |
| `--process` | generic | `generic` or `sg13g2` |
| `--max-sims` | 15 | Simulation budget (guardrail) |
| `--effort` | high | Claude reasoning effort: low, medium, high, xhigh, max |

## Circuit

| Device | Role | Sized by |
|---|---|---|
| M1, M2 | NMOS input pair | `w1`, `l1` |
| M3, M4 | PMOS mirror load | `w3`, `l3` |
| M5, M8 | Tail source and bias diode (same size) | `w5`, `l5`, `ibias` |
| M6 | Second-stage PMOS common source | `w6`, `l6` |
| M7 | Second-stage NMOS sink, mirrors M8 | `w7` (length `l5`) |
| Cc, Rz | Miller compensation with nulling resistor | `cc`, `rz` |

The testbench closes the loop at DC through a very large inductor, so the output is biased at the input common mode while the AC analysis sees the open loop. Saturation is checked as |Vds| > |Vdsat| + 50 mV.

## Equation-first methodology

```
knobs (Claude) ──► design_from_equations ──► design_id + sizes + predicted metrics
                                                  │
                                       simulate_design(design_id)   (ngspice)
                                                  │
                 prediction vs simulation (% error) + process parameters extracted from operating points
                                                  │
        recalibrate_process (values from simulation only)   or   new knobs   ──► back to the equations
```

| Tool | Does | Guardrail |
|---|---|---|
| `design_from_equations` | Runs the equations with Claude's knobs and the current process parameters | Knob ranges; no simulation cost |
| `simulate_design` | Simulates a calculated design; returns metrics, operating points, prediction errors, extracted parameters | Only accepts `design_id`s from the equations; uses the budget |
| `recalibrate_process` | Replaces chosen process parameters with values extracted from a simulation | Values come from the simulation, never from Claude; limited count |

The equations in `sizing_agent/equations.py` are the textbook square-law procedure (Allen & Holberg). They are a stand-in: replace `design()` and `extract()` with your own methodology and the rest of the loop stays the same.

### First results without the LLM (`--check`)

Default knobs, recalibrating all parameters after every pass, SG13G2 typical corner:

| Pass | Gain dB pred / sim | GBW MHz pred / sim | PM deg pred / sim | Power mW pred / sim |
|---|---|---|---|---|
| 1 | 64.8 / 40.5 | 50.0 / 31.9 | 68.2 / 71.9 | 0.355 / 0.413 |
| 2 | 52.0 / 46.7 | 50.0 / 37.0 | 68.2 / 68.2 | 0.315 / 0.362 |
| 3 | 46.6 / 46.5 | 50.0 / 37.5 | 68.2 / 68.1 | 0.316 / 0.366 |
| 4 | 46.4 / 46.4 | 50.0 / 37.6 | 68.2 / 68.1 | 0.316 / 0.366 |

- Back-annotation converges in two passes: gain and phase margin are then predicted within 0.5 %.
- GBW stays 33 % optimistic although simulated gm1 is within 4 % of target. The gap is the capacitance at the first-stage output (Cgs of M6), which GBW = gm1/Cc ignores. Recalibration cannot fix a missing term; better equations or different knobs must.
- Power is 14 % low: the M7/M8 mirror ratio shifts with their different Vds.
- The spec is not met with the default knobs (gain 46 dB). Choosing knobs that meet it is the agent's job.

### Agent run on SG13G2 (Claude in the loop)

Full report: [docs/runs/2026-10-15-sg13g2-agent-run.md](docs/runs/2026-10-15-sg13g2-agent-run.md). Spec 60 dB, 50 MHz, 60°, 1 mW, CL = 2 pF.

| Stage | Gain dB | GBW MHz | PM deg | Power mW |
|---|---|---|---|---|
| D1, generic process parameters | 43.4 | 31.2 | 60.8 | 0.49 |
| D9, best with Rz fixed at 1/gm6 | 60.1 | 42.9 | 51.9 | 0.59 |
| **D17, with the `rz_ratio` knob (recommended)** | **60.5** | **52.5** | **68.8** | **0.445** |

12 of 15 simulations, 18 calculated designs, 5 selective recalibrations. After back-annotation, power is predicted within 1–3 % and gain within 1–2.5 dB. Phase margin and GBW are not: the textbook equations miss the first-stage capacitance, the third pole from Rz and the mirror pole. Gain margin is thin; more gain needs a structural change (cascode, or separate L for M3 and M6).

## Project layout

```
sizing_agent/
  circuit.py     parameters and bounds, spec, netlist template, process definitions (generic, SG13G2)
  equations.py   hand-analysis design equations and back-annotation (replace with your own)
  eq_tools.py    equation-first tools: design_from_equations, simulate_design, recalibrate_process
  simulator.py   runs ngspice, parses metrics and operating points
  tools.py       free-mode tool: simulate_ota
  agent.py       the hand-written agent loop (Claude API, tool use)
  __main__.py    command line and run summary
models/generic130.lib   generic BSIM4 models (not a real PDK)
examples/sg13g2/        running on the IHP SG13G2 open PDK (tested)
scripts/bootstrap.sh    one-step setup: .venv + SG13G2 toolchain in .tools/
scripts/setup_sg13g2.sh the toolchain part (PDK models, OpenVAF-Reloaded, ngspice 45)
.claude/hooks/          session-start hook for Claude Code cloud sessions
docs/runs/              reports of agent runs
tests/                  simulator, equation and SG13G2 tests, and a fake-client test of the loop
```

## Limitations

- **Models:** `generic130.lib` is a generic BSIM4 card, useful for learning and trends only. Use `--process sg13g2` for a real (open) process.
- **Coverage:** only typical conditions are simulated: no corners, Monte Carlo, noise, slew rate, output swing or CMRR yet.
- **Cost:** each agent run makes one Claude API call per step. Check token usage at the end of the run.

## Roadmap

- [x] Run on IHP SG13G2 (typical corner)
- [x] Equation-first methodology with back-annotation
- [x] Nulling-resistor knob (`rz_ratio`) with a left-half-plane zero
- [ ] Replace the textbook equations with your own methodology
- [ ] gm/ID lookup tables instead of square-law
- [ ] Cadence Spectre backend
- [ ] Corner and Monte Carlo tool
- [ ] Slew rate, output swing and noise in the spec
- [ ] Compare with Bayesian optimization (Optuna) on the same spec: simulations needed and final power
- [ ] Plot the trajectory (metrics per simulation)

## License

MIT
