"""Two-stage Miller OTA: design parameters, bounds, spec and netlist template."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# name: (unit, min, max, description)
PARAMS: dict[str, tuple[str, float, float, str]] = {
    "w1": ("um", 0.15, 200.0, "Input pair M1/M2 width"),
    "l1": ("um", 0.13, 5.0, "Input pair M1/M2 length"),
    "w3": ("um", 0.15, 200.0, "PMOS mirror load M3/M4 width"),
    "l3": ("um", 0.13, 5.0, "PMOS mirror load M3/M4 length"),
    "w5": ("um", 0.15, 200.0, "Tail source M5 and bias diode M8 width"),
    "l5": ("um", 0.13, 5.0, "Length of M5, M7 and M8 (NMOS current mirrors)"),
    "w6": ("um", 0.15, 400.0, "Second-stage PMOS M6 width"),
    "l6": ("um", 0.13, 5.0, "Second-stage PMOS M6 length"),
    "w7": ("um", 0.15, 400.0, "Second-stage NMOS sink M7 width (current = ibias * w7/w5)"),
    "ibias": ("uA", 1.0, 200.0, "Reference current into bias diode M8; tail current equals ibias because M5 = M8"),
    "cc": ("pF", 0.05, 10.0, "Miller compensation capacitor"),
    "rz": ("kOhm", 0.001, 100.0, "Nulling resistor in series with Cc"),
}

TRANSISTORS = ["m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8"]
POLARITY = {"m1": "n", "m2": "n", "m3": "p", "m4": "p", "m5": "n", "m6": "p", "m7": "n", "m8": "n"}

# A reasonable, not-yet-optimized starting point (meets gain, misses GBW).
DEFAULT_DESIGN = {
    "w1": 10.0, "l1": 0.5, "w3": 10.0, "l3": 0.5, "w5": 10.0, "l5": 0.5,
    "w6": 80.0, "l6": 0.25, "w7": 40.0, "ibias": 20.0, "cc": 1.0, "rz": 2.0,
}


@dataclass
class Spec:
    gain_db_min: float = 60.0
    gbw_hz_min: float = 50e6
    pm_deg_min: float = 60.0
    power_w_max: float = 1e-3
    vdd: float = 1.2
    vcm: float = 0.8
    cl_pf: float = 2.0

    def describe(self) -> str:
        return (
            f"DC gain >= {self.gain_db_min:g} dB, unity-gain bandwidth >= {self.gbw_hz_min / 1e6:g} MHz, "
            f"phase margin >= {self.pm_deg_min:g} deg, power <= {self.power_w_max * 1e3:g} mW, "
            f"VDD = {self.vdd:g} V, input common mode = {self.vcm:g} V, load CL = {self.cl_pf:g} pF"
        )


# Canonical operating-point quantities -> name used by the device model
BSIM_OP_NAMES = {"id": "id", "gm": "gm", "gds": "gds", "vds": "vds", "vdsat": "vdsat", "vgs": "vgs", "vth": "vth"}
PSP_OP_NAMES = {"id": "ids", "gm": "gm", "gds": "gds", "vds": "vds", "vdsat": "vdss", "vgs": "vgs", "vth": "vth"}


@dataclass
class Process:
    """Everything that depends on the technology and the simulator installation.

    Swap this object to change PDK; the circuit, equations and agent stay the same.
    """

    name: str = "generic130 (BSIM4, not a real PDK)"
    include: str = field(default_factory=lambda: f".include {MODELS_DIR / 'generic130.lib'}")
    nmos: str = "nch"
    pmos: str = "pch"
    lmin_um: float = 0.13
    # "M": plain MOSFET models. "X": the PDK wraps devices in subcircuits.
    instance_prefix: str = "M"
    # How to reach an operating-point value. {inst}, {model} and {q} are filled in.
    op_ref: str = "@{inst}[{q}]"
    op_names: dict = field(default_factory=lambda: dict(BSIM_OP_NAMES))
    # Written to .spiceinit next to the netlist (e.g. to load OSDI device models)
    spiceinit: str = ""
    ngspice: str = field(default_factory=lambda: os.environ.get("NGSPICE", "ngspice"))
    # Split wide devices into fingers (ng) no wider than this; None = single finger.
    max_finger_um: float | None = None

    def fingers(self, w_um: float) -> int:
        return max(1, math.ceil(w_um / self.max_finger_um - 1e-9)) if self.max_finger_um else 1

    def device(self, name: str, d: str, g: str, s: str, b: str, pol: str, w_um: float, l_um: float) -> str:
        model = self.nmos if pol == "n" else self.pmos
        line = f"{self.instance_prefix}{name[1:]} {d} {g} {s} {b} {model} W={w_um:g}u L={l_um:g}u"
        ng = self.fingers(w_um)
        return line + (f" ng={ng}" if ng > 1 else "")

    def op(self, name: str, pol: str, quantity: str) -> str:
        inst = (self.instance_prefix + name[1:]).lower()
        model = self.nmos if pol == "n" else self.pmos
        return self.op_ref.format(inst=inst, model=model, q=self.op_names[quantity])


def generic130() -> Process:
    return Process()


def sg13g2(pdk_root: str | None = None, corner: str = "mos_tt") -> Process:
    """IHP SG13G2 low-voltage MOS (open PDK). Needs ngspice >= 44 with the PSP103 OSDI model.

    pdk_root: folder that contains ihp-sg13g2/ (defaults to $PDK_ROOT).
    """
    root = Path(pdk_root or os.environ.get("PDK_ROOT", "")) / "ihp-sg13g2" / "libs.tech"
    osdi = Path(os.environ.get("SG13G2_OSDI", root / "ngspice" / "osdi" / "psp103.osdi"))
    lib = root / "ngspice" / "models" / "cornerMOSlv.lib"
    return Process(
        name=f"IHP SG13G2 low-voltage MOS ({corner})",
        include=f".lib {lib} {corner}",
        nmos="sg13_lv_nmos",
        pmos="sg13_lv_pmos",
        lmin_um=0.13,
        instance_prefix="X",
        op_ref="@n.{inst}.n{model}[{q}]",
        op_names=dict(PSP_OP_NAMES),
        spiceinit=f"osdi {osdi}",
        # PSP gives unphysical thresholds for very wide single fingers; real layouts use fingers anyway.
        max_finger_um=5.0,
    )


def validate(params: dict[str, float]) -> list[str]:
    """Return a list of problems; empty means the parameter set is usable."""
    errors = []
    for name in params:
        if name not in PARAMS:
            errors.append(f"Unknown parameter '{name}'. Valid: {', '.join(PARAMS)}")
    for name, (unit, lo, hi, _) in PARAMS.items():
        if name not in params:
            errors.append(f"Missing parameter '{name}'")
            continue
        value = params[name]
        if not isinstance(value, (int, float)):
            errors.append(f"'{name}' must be a number")
        elif not lo <= value <= hi:
            errors.append(f"'{name}'={value} {unit} is outside [{lo}, {hi}] {unit}")
    return errors


def netlist(p: dict[str, float], spec: Spec, proc: Process) -> str:
    """Open-loop AC testbench. A huge L/C pair closes the loop at DC only,
    so the output is biased at the common-mode level while AC sees the open loop."""
    dev = lambda name, d, g, s_, b, w, l: proc.device(name, d, g, s_, b, POLARITY[name], w, l)  # noqa: E731
    save_ops = "\n".join(
        f"let {m}_{q}={proc.op(m, POLARITY[m], q)}\necho \"OP {m} {q}=$&{m}_{q}\""
        for m in TRANSISTORS
        for q in ("id", "gm", "gds", "vds", "vdsat", "vgs", "vth")
    )
    devices = "\n".join([
        dev("m1", "x1", "inn", "tail", "0", p["w1"], p["l1"]),
        dev("m2", "x2", "inp", "tail", "0", p["w1"], p["l1"]),
        dev("m3", "x1", "x1", "vdd", "vdd", p["w3"], p["l3"]),
        dev("m4", "x2", "x1", "vdd", "vdd", p["w3"], p["l3"]),
        dev("m5", "tail", "nb", "0", "0", p["w5"], p["l5"]),
        dev("m8", "nb", "nb", "0", "0", p["w5"], p["l5"]),
        dev("m6", "out", "x2", "vdd", "vdd", p["w6"], p["l6"]),
        dev("m7", "out", "nb", "0", "0", p["w7"], p["l5"]),
    ])
    return f"""* Two-stage Miller OTA - generated by sizing_agent ({proc.name})
{proc.include}
VDD vdd 0 {spec.vdd}
VCM cm 0 {spec.vcm}
VIN inp cm dc 0 ac 1
Lfb out inn 1e6
Cfb inn 0 1e6

{devices}
IB vdd nb {p['ibias']:g}u
Cc x2 mid {p['cc']:g}p
Rz mid out {p['rz']:g}k
CL out 0 {spec.cl_pf:g}p

.control
op
let pwr = -vdd#branch*{spec.vdd}
echo "RESULT power_w=$&pwr"
let vout_dc = v(out)
echo "RESULT vout_dc=$&vout_dc"
{save_ops}
ac dec 50 1 10G
let phd = 180/PI*cph(out)
meas ac gain_db FIND vdb(out) AT=1
meas ac ugf_hz WHEN vdb(out)=0 FALL=1
meas ac phase_ugf FIND phd WHEN vdb(out)=0 FALL=1
echo "RESULT gain_db=$&gain_db"
echo "RESULT ugf_hz=$&ugf_hz"
echo "RESULT phase_ugf=$&phase_ugf"
quit
.endc
.end
"""
