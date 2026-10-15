# analog-sizing-agent

An LLM agent that sizes a **two-stage Miller-compensated OTA** with **ngspice in the loop**. Claude proposes transistor sizes from hand analysis, runs a simulation through a tool, reads the gain, bandwidth, phase margin, power and operating points, and adjusts until the spec is met or the simulation budget runs out.

```
spec ──► Claude (gm/Id reasoning) ──► simulate_ota(sizes) ──► ngspice
              ▲                                                  │
              └──── metrics + operating points + pass/fail ◄─────┘
```

The agent loop is written by hand in `sizing_agent/agent.py`, without a framework, so every step is visible.

## Quick start

```bash
# 1. ngspice (version 38 or later)
sudo apt install ngspice        # macOS: brew install ngspice

# 2. Python package
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 3. Check the simulator without any API call
python -m sizing_agent --check
pytest -q

# 4. Run the agent
export ANTHROPIC_API_KEY=...    # from console.anthropic.com
python -m sizing_agent
```

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
| `--max-sims` | 25 | Simulation budget (guardrail) |
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

## Project layout

```
sizing_agent/
  circuit.py     parameters and bounds, spec, netlist template
  simulator.py   runs ngspice, parses metrics and operating points
  tools.py       tool schema, simulation budget, history, best design
  agent.py       the hand-written agent loop (Claude API, tool use)
  __main__.py    command line and run summary
models/generic130.lib   generic BSIM4 models (not a real PDK)
examples/sg13g2/        how to move to the IHP SG13G2 open PDK
tests/                  simulator tests and a fake-client test of the loop
```

## Limitations

- **Models:** `generic130.lib` is a generic BSIM4 card, useful for learning and trends but not for real design numbers. See `examples/sg13g2/README.md` for moving to IHP SG13G2.
- **Coverage:** only typical conditions are simulated: no corners, Monte Carlo, noise, slew rate, output swing or CMRR yet.
- **Cost:** each agent run makes one Claude API call per step. Check token usage at the end of the run.

## Roadmap

- [ ] Run on IHP SG13G2 (typical corner)
- [ ] Corner and Monte Carlo tool
- [ ] Slew rate, output swing and noise in the spec
- [ ] Compare with Bayesian optimization (Optuna) on the same spec: simulations needed and final power
- [ ] Plot the trajectory (metrics per simulation)

## License

MIT
