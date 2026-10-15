"""Command line entry point: python -m sizing_agent [--check] [spec options]"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .circuit import DEFAULT_DESIGN, PARAMS, Spec, generic130, sg13g2
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


def scripted_loop(session, passes: int) -> str:
    """Methodology check without the LLM: default knobs, simulate, recalibrate everything, repeat.

    Shows whether back-annotation alone makes the equations converge to the simulator.
    """
    from .equations import DEFAULT_KNOBS

    rows = ["| Pass | Gain dB pred / sim | GBW MHz pred / sim | PM deg pred / sim | Power mW pred / sim |", "|---|---|---|---|---|"]
    for i in range(1, passes + 1):
        out, err = session.run_tool("design_from_equations", {"rationale": f"scripted pass {i}", **DEFAULT_KNOBS})
        did = json.loads(out)["design_id"]
        out, err = session.run_tool("simulate_design", {"design_id": did})
        if err:
            print(out)
            break
        print(session.console_line("simulate_design", {}, out, err))
        d = session.designs[did]
        rows.append(f"| {i} | " + " | ".join(f"{d['predicted'][m]} / {d['simulated']['metrics'][m]}"
                                           for m in ("gain_db", "gbw_mhz", "phase_margin_deg", "power_mw")) + " |")
        out, err = session.run_tool("recalibrate_process", {"from_design_id": did, "parameters": list(d["extracted"]),
                                                              "rationale": "scripted: take all extracted values"})
        print(session.console_line("recalibrate_process", {}, out, err))
    return "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM agent that sizes a two-stage OTA with ngspice in the loop.")
    ap.add_argument("--mode", default="equations", choices=["equations", "free"],
                    help="equations: equation-first methodology (default). free: the agent picks sizes directly.")
    ap.add_argument("--process", default="generic", choices=["generic", "sg13g2"],
                    help="generic: built-in BSIM4 cards. sg13g2: IHP open PDK (needs PDK_ROOT and ngspice >= 44 with PSP OSDI).")
    ap.add_argument("--pdk-root", default=None, help="Folder containing ihp-sg13g2/ (default: $PDK_ROOT).")
    ap.add_argument("--check", action="store_true",
                    help="No API call. free mode: simulate the default design once. equations mode: run a scripted equations/simulate/recalibrate loop.")
    ap.add_argument("--passes", type=int, default=4, help="Passes for the scripted --check loop in equations mode.")
    ap.add_argument("--gain-db", type=float, default=60.0)
    ap.add_argument("--gbw-mhz", type=float, default=50.0)
    ap.add_argument("--pm-deg", type=float, default=60.0)
    ap.add_argument("--power-mw", type=float, default=1.0)
    ap.add_argument("--cl-pf", type=float, default=2.0)
    ap.add_argument("--max-sims", type=int, default=15, help="Simulation budget (guardrail).")
    ap.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--model", default=None, help="Claude model ID (default: claude-opus-5-5).")
    args = ap.parse_args()

    spec = Spec(gain_db_min=args.gain_db, gbw_hz_min=args.gbw_mhz * 1e6, pm_deg_min=args.pm_deg,
                power_w_max=args.power_mw * 1e-3, cl_pf=args.cl_pf)
    proc = sg13g2(args.pdk_root) if args.process == "sg13g2" else generic130()
    log_dir = Path("runs") / (datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{args.mode}-{args.process}")

    if args.mode == "free" and args.check:
        print(json.dumps(simulate(DEFAULT_DESIGN, spec, proc), indent=1))
        return

    if args.mode == "equations":
        from .eq_tools import EquationSession

        session = EquationSession(spec, proc, args.max_sims if not args.check else args.passes, log_dir)
        if args.check:
            table = scripted_loop(session, args.passes)
            print("\n" + table)
            (log_dir / "summary.md").write_text(session.summary_markdown("(scripted check, no agent)"))
            session.close()
            print(f"\nSummary written to {log_dir / 'summary.md'}")
            return
    else:
        session = SizingSession(spec, proc, args.max_sims, log_dir)

    from .agent import MODEL, run_agent  # imported here so --check works without an API key

    final_text = ""
    try:
        final_text = run_agent(session, model=args.model or MODEL, effort=args.effort)
    finally:
        if args.mode == "equations":
            summary = log_dir / "summary.md"
            summary.write_text(session.summary_markdown(final_text))
        else:
            summary = write_summary(session, final_text)
        session.close()
    print(f"\nSummary written to {summary}")


if __name__ == "__main__":
    main()
