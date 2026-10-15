"""The tool the agent can call, plus the bookkeeping around it (budget, history, best design)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .circuit import PARAMS, Process, Spec, validate
from .simulator import SimulationError, simulate

SIMULATE_TOOL = {
    "name": "simulate_ota",
    "description": (
        "Run an ngspice operating-point and open-loop AC simulation of the two-stage OTA with the given sizes. "
        "Returns DC gain, unity-gain bandwidth, phase margin, power, a pass/fail check against the spec, and per-transistor "
        "operating points (Id, gm, gm/Id, Vgs, Vth, Vds, Vdsat, region). Every call uses one simulation from the budget. "
        "All 12 sizing parameters are required on every call. Ranges: "
        + "; ".join(f"{k} {lo}-{hi} {u}" for k, (u, lo, hi, _) in PARAMS.items())
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": "One or two sentences: what you changed since the last run and why you expect it to help.",
            },
            **{
                name: {"type": "number", "description": f"{desc} [{unit}]"}
                for name, (unit, _, _, desc) in PARAMS.items()
            },
        },
        "required": ["rationale", *PARAMS],
        "additionalProperties": False,
    },
}


class SizingSession:
    """Free sizing: the agent picks transistor sizes directly and simulates them."""

    tools = [SIMULATE_TOOL]
    first_message = "Size the OTA to meet the spec. Start with a hand-calculated first guess."

    def __init__(self, spec: Spec, proc: Process, max_sims: int, log_dir: Path):
        self.spec = spec
        self.proc = proc
        self.max_sims = max_sims
        self.history: list[dict] = []
        self.log_dir = log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log = (log_dir / "log.jsonl").open("a")

    @property
    def sims_used(self) -> int:
        return len(self.history)

    def log(self, event: str, **data) -> None:
        self._log.write(json.dumps({"t": round(time.time(), 2), "event": event, **data}, default=str) + "\n")
        self._log.flush()

    def run_tool(self, name: str, tool_input: dict) -> tuple[str, bool]:
        """Execute one tool call. Returns (content for tool_result, is_error)."""
        if name != SIMULATE_TOOL["name"]:
            return f"Unknown tool '{name}'.", True
        if self.sims_used >= self.max_sims:
            return ("Simulation budget exhausted. Do not call the tool again; give your final answer "
                    "with the best design found so far."), True

        rationale = tool_input.get("rationale", "")
        params = {k: v for k, v in tool_input.items() if k != "rationale"}
        errors = validate(params)
        if errors:
            self.log("invalid_input", input=tool_input, errors=errors)
            return "Invalid parameters (no simulation used):\n- " + "\n- ".join(errors), True

        try:
            result = simulate(params, self.spec, self.proc)
        except SimulationError as exc:
            self.history.append({"params": params, "rationale": rationale, "error": str(exc)})
            self.log("sim_error", params=params, error=str(exc))
            return f"Simulation failed: {exc}", True

        self.history.append({"params": params, "rationale": rationale, **result})
        self.log("simulation", n=self.sims_used, params=params, rationale=rationale, result=result)
        payload = {
            "simulation": f"{self.sims_used} of {self.max_sims}",
            **result,
        }
        return json.dumps(payload, indent=1), False

    def system_prompt(self) -> str:
        from .agent import build_system_prompt
        return build_system_prompt(self.spec, self.proc, self.max_sims)

    def console_line(self, name: str, tool_input: dict, content: str, is_error: bool) -> str:
        if is_error:
            return f"[tool error] {content.splitlines()[0]}"
        last = self.history[-1]
        m = last["metrics"]
        status = "MEETS SPEC" if last["meets_spec"] else "fails: " + ", ".join(k for k, v in last["spec_check"].items() if not v)
        return (f"[sim {self.sims_used}/{self.max_sims}] gain {m['gain_db']} dB | GBW {m['gbw_mhz']} MHz | "
                f"PM {m['phase_margin_deg']} deg | P {m['power_mw']} mW | {status}")

    def best(self) -> dict | None:
        """Lowest-power design that meets everything; otherwise the one passing the most checks."""
        ok = [h for h in self.history if "metrics" in h]
        if not ok:
            return None
        return max(
            ok,
            key=lambda h: (h["meets_spec"], sum(h["spec_check"].values()), -h["metrics"]["power_mw"]),
        )

    def close(self) -> None:
        self._log.close()
