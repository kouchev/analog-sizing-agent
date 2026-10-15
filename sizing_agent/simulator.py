"""Run ngspice in batch mode and turn its output into metrics the agent can read."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .circuit import TRANSISTORS, Process, Spec, netlist

SAT_MARGIN_V = 0.05  # |Vds| must exceed |Vdsat| by this much to count as saturated

_RESULT = re.compile(r"^RESULT (\w+)=(\S+)", re.M)
_OP = re.compile(r"^OP (m\d) (\w+)=(\S+)", re.M)


class SimulationError(RuntimeError):
    pass


def _num(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None  # e.g. a failed .meas leaves the variable undefined


def run_ngspice(net: str, exe_name: str = "ngspice", spiceinit: str = "", timeout_s: float = 60.0) -> str:
    exe = shutil.which(exe_name)
    if exe is None:
        raise SimulationError(f"'{exe_name}' not found. Install ngspice (e.g. 'apt install ngspice') or set NGSPICE to its path.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ota.cir"
        path.write_text(net)
        if spiceinit:
            (Path(tmp) / ".spiceinit").write_text(spiceinit + "\n")
        try:
            proc = subprocess.run([exe, "-b", str(path)], capture_output=True, text=True, timeout=timeout_s, cwd=tmp)
        except subprocess.TimeoutExpired as exc:
            raise SimulationError(f"ngspice timed out after {timeout_s:g} s") from exc
    return proc.stdout + proc.stderr


def simulate(params: dict[str, float], spec: Spec, proc: Process) -> dict:
    """Simulate one design point. Returns metrics, spec check and per-device operating points."""
    out = run_ngspice(netlist(params, spec, proc), proc.ngspice, proc.spiceinit)
    res = {k: _num(v) for k, v in _RESULT.findall(out)}
    if res.get("power_w") is None:
        tail = "\n".join(out.strip().splitlines()[-15:])
        raise SimulationError(f"Operating point failed. Last ngspice output:\n{tail}")

    ops: dict[str, dict[str, float | None]] = {m: {} for m in TRANSISTORS}
    for m, q, v in _OP.findall(out):
        ops[m][q] = _num(v)

    devices = {}
    for m, op in ops.items():
        vds, vdsat, gm, idd = (op.get(k) for k in ("vds", "vdsat", "gm", "id"))
        region = "unknown"
        if vds is not None and vdsat is not None:
            region = "saturation" if abs(vds) > abs(vdsat) + SAT_MARGIN_V else "TRIODE"
        devices[m] = {
            "id_uA": _r(abs(idd) * 1e6 if idd is not None else None),
            "gm_uS": _r(gm * 1e6 if gm is not None else None),
            "gds_uS": _r(abs(op["gds"]) * 1e6 if op.get("gds") is not None else None),
            "gm_over_id": _r(abs(gm / idd) if gm and idd else None),
            "vgs_V": _r(op.get("vgs")),
            "vth_V": _r(op.get("vth")),
            "vds_V": _r(vds),
            "vdsat_V": _r(vdsat),
            "region": region,
        }

    gain = res.get("gain_db")
    ugf = res.get("ugf_hz")
    ph = res.get("phase_ugf")
    pm = 180.0 + ph if ph is not None else None
    metrics = {
        "gain_db": _r(gain),
        "gbw_mhz": _r(ugf / 1e6 if ugf is not None else None),
        "phase_margin_deg": _r(pm),
        "power_mw": _r(res["power_w"] * 1e3),
        "vout_dc_V": _r(res.get("vout_dc")),
    }
    checks = {
        "gain": gain is not None and gain >= spec.gain_db_min,
        "gbw": ugf is not None and ugf >= spec.gbw_hz_min,
        "phase_margin": pm is not None and pm >= spec.pm_deg_min,
        "power": res["power_w"] <= spec.power_w_max,
        "all_saturated": all(d["region"] == "saturation" for d in devices.values()),
    }
    return {
        "metrics": metrics,
        "spec_check": checks,
        "meets_spec": all(checks.values()),
        "devices": devices,
    }


def _r(x: float | None, nd: int = 4) -> float | None:
    if x is None:
        return None
    return float(f"{x:.{nd}g}")
