"""Equation-first mode: tools and bookkeeping.

The loop the agent must follow:
    design_from_equations(knobs) -> simulate_design(design_id) -> compare
        -> recalibrate_process(from simulation) and/or new knobs -> design_from_equations ...

The simulator only accepts design_ids produced by the equations, so every simulated
design is traceable to the methodology.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from .circuit import Process, Spec
from .equations import KNOBS, ProcessParams, design, extract, validate_knobs
from .simulator import SimulationError, simulate

METRICS = ("gain_db", "gbw_mhz", "phase_margin_deg", "power_mw")

DESIGN_TOOL = {
    "name": "design_from_equations",
    "description": (
        "Run the hand-analysis design equations with the given design knobs and the CURRENT process parameters. "
        "Returns a design_id, the transistor sizes, every intermediate value (the derivation), warnings, and the "
        "predicted gain, GBW, phase margin and power. Does not use the simulation budget. Knob ranges: "
        + "; ".join(f"{k} {lo}-{hi}" for k, (_, lo, hi, _) in KNOBS.items())
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {"type": "string", "description": "Why these knob values, in one or two sentences."},
            **{k: {"type": "number", "description": desc} for k, (_, _, _, desc) in KNOBS.items()},
        },
        "required": ["rationale", *KNOBS],
        "additionalProperties": False,
    },
}

SIMULATE_TOOL = {
    "name": "simulate_design",
    "description": (
        "Simulate a design produced by design_from_equations (ngspice: operating point and open-loop AC). "
        "Returns simulated metrics, spec pass/fail, per-transistor operating points, a prediction-vs-simulation "
        "table with errors in %, and the process parameters extracted from this simulation next to the values "
        "the equations used. Uses one simulation from the budget."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"design_id": {"type": "string", "description": "An id returned by design_from_equations, e.g. 'D3'."}},
        "required": ["design_id"],
        "additionalProperties": False,
    },
}

RECALIBRATE_TOOL = {
    "name": "recalibrate_process",
    "description": (
        "Replace selected process parameters with the values extracted from a simulated design (back-annotation). "
        "You choose which parameters; the values always come from that simulation's operating points, never from you. "
        f"Parameters: {', '.join(ProcessParams.__dataclass_fields__)}."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "from_design_id": {"type": "string", "description": "A design_id that has been simulated."},
            "parameters": {
                "type": "array",
                "items": {"type": "string", "enum": list(ProcessParams.__dataclass_fields__)},
                "description": "Which process parameters to update.",
            },
            "rationale": {"type": "string", "description": "Which mismatch this should fix."},
        },
        "required": ["from_design_id", "parameters", "rationale"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You are an experienced analog IC designer. You size a two-stage Miller-compensated OTA with an equation-first methodology, and you must follow it:

1. Every design starts from the hand-analysis equations (design_from_equations). You choose the design knobs; the equations compute the sizes. You never choose transistor sizes directly.
2. You verify each calculated design in the simulator (simulate_design with its design_id).
3. You compare prediction and simulation:
   - If the prediction is clearly off (roughly >15 % on a metric, or a parameter the equations rely on differs a lot from the extracted value), the equations are using wrong process parameters: recalibrate the relevant ones from the simulation (recalibrate_process), then recalculate.
   - If the prediction matches but the spec fails, change the knobs using design reasoning, then recalculate.
   - Fix devices out of saturation and equation warnings first; they make the prediction meaningless.
4. Stop when the spec is met with some margin and prediction and simulation agree reasonably, or when the budget is nearly spent.

Process: {process}. Minimum channel length {lmin} um.
Topology: NMOS input pair M1/M2 with PMOS mirror load M3/M4, NMOS tail M5 biased by diode M8 (same size as M5) from the reference current, second stage PMOS common source M6 with NMOS sink M7 (mirrors M8), Miller capacitor Cc with nulling resistor Rz, load CL. The testbench closes the loop at DC only, so the output sits at the input common mode ({vcm} V) and the AC result is open loop.

Spec: {spec}. All transistors in saturation.
Budget: {max_sims} simulations. Equation runs and recalibrations do not use the budget, but keep them purposeful.

Knobs:
{knobs}

Process parameters the equations use (starting values are generic guesses): {pparams}.

The equations are the textbook square-law procedure (Allen & Holberg). Their known weak spots are moderate/weak inversion (gm/Id above about 15 1/V), parasitic capacitance at the first-stage output (Cgs of a wide M6), and channel-length modulation at short L. Watch for these when prediction and simulation disagree.

Final answer: the design_id you recommend, the knob values, the sizes, predicted vs simulated metrics, how many passes were needed, and what you learned about where the equations are accurate or not."""


class EquationSession:
    """State for one equation-first sizing run."""

    tools = [DESIGN_TOOL, SIMULATE_TOOL, RECALIBRATE_TOOL]
    first_message = "Size the OTA with the equation-first methodology. Start by choosing knobs for a first calculated design."

    def __init__(self, spec: Spec, proc: Process, max_sims: int, log_dir: Path, pparams: ProcessParams | None = None,
                 max_recalibrations: int = 8):
        self.spec = spec
        self.proc = proc
        self.max_sims = max_sims
        self.max_recalibrations = max_recalibrations
        self.pparams = pparams or ProcessParams()
        self.designs: dict[str, dict] = {}
        self.history: list[dict] = []          # one entry per simulation
        self.recalibrations: list[dict] = []
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log = (log_dir / "log.jsonl").open("a")

    # ----- bookkeeping -------------------------------------------------------
    @property
    def sims_used(self) -> int:
        return len(self.history)

    def log(self, event: str, **data) -> None:
        self._log.write(json.dumps({"t": round(time.time(), 2), "event": event, **data}, default=str) + "\n")
        self._log.flush()

    def close(self) -> None:
        self._log.close()

    def system_prompt(self) -> str:
        knobs = "\n".join(f"- {k} ({lo}-{hi}, default {d}): {desc}" for k, (d, lo, hi, desc) in KNOBS.items())
        pp = ", ".join(f"{k}={v:g}" for k, v in asdict(self.pparams).items())
        return SYSTEM_PROMPT.format(process=self.proc.name, lmin=self.proc.lmin_um, vcm=self.spec.vcm,
                                    spec=self.spec.describe(), max_sims=self.max_sims, knobs=knobs, pparams=pp)

    # ----- tools -------------------------------------------------------------
    def run_tool(self, name: str, tool_input: dict) -> tuple[str, bool]:
        handler = {
            "design_from_equations": self._design,
            "simulate_design": self._simulate,
            "recalibrate_process": self._recalibrate,
        }.get(name)
        if handler is None:
            return f"Unknown tool '{name}'.", True
        return handler(tool_input)

    def _design(self, inp: dict) -> tuple[str, bool]:
        knobs = {k: v for k, v in inp.items() if k != "rationale"}
        errors = validate_knobs(knobs)
        if errors:
            return "Invalid knobs:\n- " + "\n- ".join(errors), True
        result = design(self.spec, knobs, self.pparams)
        did = f"D{len(self.designs) + 1}"
        self.designs[did] = {
            "knobs": knobs, "rationale": inp.get("rationale", ""), "pparams": asdict(self.pparams),
            "sizes": result.sizes, "predicted": result.predicted, "steps": result.steps,
            "warnings": result.warnings, "simulated": None,
        }
        self.log("design", design_id=did, **self.designs[did])
        return json.dumps({"design_id": did, **result.as_dict()}, indent=1), False

    def _simulate(self, inp: dict) -> tuple[str, bool]:
        did = inp.get("design_id", "")
        d = self.designs.get(did)
        if d is None:
            return f"Unknown design_id '{did}'. Only designs from design_from_equations can be simulated.", True
        if d["simulated"] is not None:
            return json.dumps({"design_id": did, "note": "Already simulated; returning the stored result.", **d["simulated"]}, indent=1), False
        if self.sims_used >= self.max_sims:
            return "Simulation budget exhausted. Give your final answer with the best simulated design.", True
        try:
            sim = simulate(d["sizes"], self.spec, self.proc)
        except SimulationError as exc:
            self.history.append({"design_id": did, "error": str(exc)})
            self.log("sim_error", design_id=did, error=str(exc))
            return f"Simulation failed: {exc}", True

        comparison = {}
        for m in METRICS:
            pred, real = d["predicted"].get(m), sim["metrics"].get(m)
            err = None
            if pred is not None and real is not None and real != 0:
                err = round((pred - real) / abs(real) * 100, 1)
            comparison[m] = {"predicted": pred, "simulated": real, "prediction_error_pct": err}
        extracted = extract(sim, d["sizes"])
        backann = {k: {"used_in_equations": d["pparams"][k], "extracted": v,
                       "ratio": round(v / d["pparams"][k], 3) if d["pparams"][k] else None}
                   for k, v in extracted.items()}
        d["simulated"] = {
            "simulation": f"{self.sims_used + 1} of {self.max_sims}",
            "meets_spec": sim["meets_spec"], "spec_check": sim["spec_check"],
            "prediction_vs_simulation": comparison, "back_annotation": backann,
            "devices": sim["devices"], "metrics": sim["metrics"],
        }
        d["extracted"] = extracted
        self.history.append({"design_id": did, **sim})
        self.log("simulation", design_id=did, result=d["simulated"])
        return json.dumps({"design_id": did, **d["simulated"]}, indent=1), False

    def _recalibrate(self, inp: dict) -> tuple[str, bool]:
        did = inp.get("from_design_id", "")
        d = self.designs.get(did)
        if d is None or d.get("simulated") is None:
            return f"'{did}' has not been simulated; recalibration needs simulated operating points.", True
        if len(self.recalibrations) >= self.max_recalibrations:
            return "Recalibration limit reached. Work with the current process parameters.", True
        names = inp.get("parameters", [])
        unknown = [n for n in names if n not in d["extracted"]]
        if unknown:
            return f"No extracted value for: {', '.join(unknown)}. Available: {', '.join(d['extracted'])}", True
        before = asdict(self.pparams)
        new = {**before, **{n: d["extracted"][n] for n in names}}
        self.pparams = ProcessParams(**new)
        change = {n: {"before": before[n], "after": new[n]} for n in names}
        self.recalibrations.append({"from": did, "changes": change, "rationale": inp.get("rationale", "")})
        self.log("recalibrate", from_design_id=did, changes=change, rationale=inp.get("rationale", ""))
        return json.dumps({"updated": change, "process_parameters_now": new,
                           "next": "Run design_from_equations again to use the new values."}, indent=1), False

    # ----- console + summary ------------------------------------------------
    def console_line(self, name: str, tool_input: dict, content: str, is_error: bool) -> str:
        if is_error:
            return f"[{name}] error: {content.splitlines()[0]}"
        data = json.loads(content)
        if name == "design_from_equations":
            p = data["predicted"]
            w = f" | {len(data['warnings'])} warning(s)" if data["warnings"] else ""
            return (f"[equations {data['design_id']}] predicted gain {p['gain_db']} dB | GBW {p['gbw_mhz']} MHz | "
                    f"PM {p['phase_margin_deg']} deg | P {p['power_mw']} mW{w}")
        if name == "simulate_design":
            c = data["prediction_vs_simulation"]
            parts = [f"{m.split('_')[0]} {c[m]['simulated']} ({c[m]['prediction_error_pct']:+}%)"
                     if c[m]["prediction_error_pct"] is not None else f"{m.split('_')[0]} {c[m]['simulated']}" for m in METRICS]
            status = "MEETS SPEC" if data["meets_spec"] else "fails: " + ", ".join(k for k, v in data["spec_check"].items() if not v)
            return f"[sim {data['design_id']}, {data['simulation']}] " + " | ".join(parts) + f" | {status}"
        if name == "recalibrate_process":
            ch = ", ".join(f"{k} {v['before']:g}->{v['after']:g}" for k, v in data["updated"].items())
            return f"[recalibrate] {ch}"
        return f"[{name}]"

    def best(self) -> dict | None:
        sims = [(did, d) for did, d in self.designs.items() if d.get("simulated")]
        if not sims:
            return None
        did, d = max(sims, key=lambda x: (x[1]["simulated"]["meets_spec"], sum(x[1]["simulated"]["spec_check"].values()),
                                          -x[1]["simulated"]["metrics"]["power_mw"]))
        return {"design_id": did, **d}

    def summary_markdown(self, final_text: str) -> str:
        lines = ["# Equation-first sizing run", "", f"Process: {self.proc.name}", f"Spec: {self.spec.describe()}", "",
                 f"Simulations used: {self.sims_used} of {self.max_sims}; recalibrations: {len(self.recalibrations)}", "",
                 "## Prediction vs simulation per pass", "",
                 "| Design | Gain dB pred / sim | GBW MHz pred / sim | PM deg pred / sim | Power mW pred / sim | Meets | Rationale |",
                 "|---|---|---|---|---|---|---|"]
        for did, d in self.designs.items():
            s = d.get("simulated")
            cells = []
            for m in METRICS:
                pred = d["predicted"][m]
                real = s["metrics"][m] if s else "–"
                cells.append(f"{pred} / {real}")
            meets = ("yes" if s["meets_spec"] else "no") if s else "not simulated"
            lines.append(f"| {did} | " + " | ".join(cells) + f" | {meets} | {d['rationale']} |")
        if self.recalibrations:
            lines += ["", "## Recalibrations", ""]
            for r in self.recalibrations:
                ch = ", ".join(f"{k}: {v['before']:g} → {v['after']:g}" for k, v in r["changes"].items())
                lines.append(f"- from {r['from']}: {ch}. {r['rationale']}")
        best = self.best()
        if best:
            lines += ["", f"## Lowest-power design that meets the spec (or passes most checks): {best['design_id']}", "", "| Knob | Value |", "|---|---|"]
            lines += [f"| {k} | {v:g} |" for k, v in best["knobs"].items()]
            lines += ["", "| Size | Value |", "|---|---|"]
            lines += [f"| {k} | {v:g} |" for k, v in best["sizes"].items()]
            lines += ["", "Derivation:", ""] + [f"- {s}" for s in best["steps"]]
        lines += ["", "## Agent's final answer", "", final_text or "(none)"]
        return "\n".join(lines) + "\n"
