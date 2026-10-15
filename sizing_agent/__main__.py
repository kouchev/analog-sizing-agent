"""Command line entry point: python -m sizing_agent [--check] [spec options]"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .circuit import DEFAULT_DESIGN, PARAMS, Process, Spec
from .simulator import simulate
from .tools import SizingSession


def write_summary(session: SizingSession, final_text: str) -> Path:
    best = session.best()
    lines = ["# Sizing run", "", f"Spec: {session.spec.describe()}", "",
             f"Simulations used: {session.sims_used} of {session.max_sims}", ""]
    if best:
        lines += ["## Best design", "", "| Parameter | Value |", "|---|---|"]
        lines += [f"| {k} | {best['params'][k]:g} {PARAMS[k][0]} |" for k in PARAMS]
        lines += ["", "| Metric | Value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in best["metrics"].items()]
        lines += ["", f"Meets spec: **{best['meets_spec']}**", ""]
    lines += ["## Trajectory", "", "| # | Gain dB | GBW MHz | PM deg | Power mW | Meets | Rationale |", "|---|---|---|---|---|---|---|"]
    for i, h in enumerate(session.history, 1):
        if "metrics" in h:
            m = h["metrics"]
            lines.append(f"| {i} | {m['gain_db']} | {m['gbw_mhz']} | {m['phase_margin_deg']} | {m['power_mw']} | "
                         f"{'yes' if h['meets_spec'] else 'no'} | {h['rationale']} |")
        else:
            lines.append(f"| {i} | | | | | error | {h['rationale']} |")
    lines += ["", "## Agent's final answer", "", final_text or "(none)"]
    path = session.log_dir / "summary.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM agent that sizes a two-stage OTA with ngspice in the loop.")
    ap.add_argument("--check", action="store_true", help="Simulate the default design once and exit (no API call).")
    ap.add_argument("--gain-db", type=float, default=60.0)
    ap.add_argument("--gbw-mhz", type=float, default=50.0)
    ap.add_argument("--pm-deg", type=float, default=60.0)
    ap.add_argument("--power-mw", type=float, default=1.0)
    ap.add_argument("--cl-pf", type=float, default=2.0)
    ap.add_argument("--max-sims", type=int, default=25, help="Simulation budget (guardrail).")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--model", default=None, help="Claude model ID (default: claude-opus-5-5).")
    args = ap.parse_args()

    spec = Spec(gain_db_min=args.gain_db, gbw_hz_min=args.gbw_mhz * 1e6, pm_deg_min=args.pm_deg,
                power_w_max=args.power_mw * 1e-3, cl_pf=args.cl_pf)
    proc = Process()

    if args.check:
        result = simulate(DEFAULT_DESIGN, spec, proc)
        print(json.dumps(result, indent=1))
        return

    from .agent import MODEL, run_agent  # imported here so --check works without an API key

    log_dir = Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S")
    session = SizingSession(spec, proc, args.max_sims, log_dir)
    try:
        final_text = run_agent(session, model=args.model or MODEL, effort=args.effort)
    finally:
        summary = write_summary(session, locals().get("final_text", ""))
        session.close()
    print(f"\nSummary written to {summary}")


if __name__ == "__main__":
    main()
