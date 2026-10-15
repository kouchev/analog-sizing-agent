"""Hand-analysis design equations for the two-stage Miller OTA.

This is the textbook square-law procedure (Allen & Holberg, CMOS Analog Circuit Design,
section 6.3), used as a stand-in until your own equations replace it. Everything the
agent may choose is a *knob*; everything that describes the technology is a *process
parameter* that back-annotation can recalibrate from simulation.

Units inside this module are SI (A, V, F, Hz, m) unless a name says otherwise.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

from .circuit import PARAMS, Spec

TWO_PI = 2 * math.pi


@dataclass
class ProcessParams:
    """Square-law parameters. Defaults are deliberately generic starting values;
    back-annotation replaces them with values extracted from simulation."""

    kn: float = 300e-6       # un*Cox [A/V^2]
    kp: float = 80e-6        # up*Cox [A/V^2]
    vthn: float = 0.40       # [V]
    vthp: float = 0.40       # |Vthp| [V]
    lam_n_um: float = 0.05   # lambda*L for NMOS [um/V]: lambda = lam_n_um / L[um]
    lam_p_um: float = 0.05   # lambda*L for PMOS [um/V]

    def lam_n(self, l_um: float) -> float:
        return self.lam_n_um / l_um

    def lam_p(self, l_um: float) -> float:
        return self.lam_p_um / l_um


# name: (default, min, max, description)
KNOBS: dict[str, tuple[float, float, float, str]] = {
    "cc_ratio": (0.25, 0.15, 1.0, "Cc / CL. Textbook minimum for 60 deg PM is 0.22"),
    "p2_ratio": (2.5, 1.5, 6.0, "Non-dominant pole p2 = gm6/CL as a multiple of GBW. 2.2 gives about 60 deg PM"),
    "rz_ratio": (1.0, 1.0, 8.0, "Nulling resistor Rz = rz_ratio/gm6. 1 moves the RHP zero to infinity; above 1 it becomes a left-half-plane zero at 1/(Cc*(Rz-1/gm6)) that adds phase. 1+CL/Cc puts it on p2"),
    "sr_v_per_us": (60.0, 1.0, 200.0, "Slew rate target in V/us; sets the tail current I5 = SR * Cc"),
    "vin_min": (0.78, 0.3, 0.9, "Lowest input common mode the design must support [V]"),
    "vin_max": (0.9, 0.5, 1.1, "Highest input common mode the design must support [V]"),
    "l1_um": (0.5, 0.13, 5.0, "Input pair length"),
    "l3_um": (0.5, 0.13, 5.0, "PMOS load length"),
    "l5_um": (0.5, 0.13, 5.0, "NMOS mirror length (M5, M7, M8)"),
    "l6_um": (0.5, 0.13, 5.0, "Second-stage PMOS length. Keep it equal to l3_um: the procedure assumes M6 and M4 have the same Vgs, otherwise the first stage gets a systematic offset"),
}

DEFAULT_KNOBS = {k: v[0] for k, v in KNOBS.items()}


@dataclass
class EquationResult:
    sizes: dict[str, float]          # in circuit.PARAMS units (um, uA, pF, kOhm)
    predicted: dict[str, float]      # same keys as simulator metrics
    steps: list[str]                 # human-readable derivation, one line per equation
    warnings: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def validate_knobs(knobs: dict[str, float]) -> list[str]:
    errors = [f"Unknown knob '{k}'. Valid: {', '.join(KNOBS)}" for k in knobs if k not in KNOBS]
    for name, (_, lo, hi, _) in KNOBS.items():
        if name not in knobs:
            errors.append(f"Missing knob '{name}'")
        elif not lo <= knobs[name] <= hi:
            errors.append(f"'{name}'={knobs[name]} is outside [{lo}, {hi}]")
    if not errors and knobs["vin_min"] >= knobs["vin_max"]:
        errors.append("vin_min must be below vin_max")
    return errors


def design(spec: Spec, k: dict[str, float], p: ProcessParams) -> EquationResult:
    """Run the design procedure. Returns sizes, predicted performance and the derivation."""
    steps: list[str] = []
    warn: list[str] = []
    cl = spec.cl_pf * 1e-12
    gbw = spec.gbw_hz_min
    vdd = spec.vdd
    l1, l3, l5, l6 = k["l1_um"], k["l3_um"], k["l5_um"], k["l6_um"]

    # 1. Compensation capacitor
    cc = k["cc_ratio"] * cl
    steps.append(f"Cc = cc_ratio*CL = {k['cc_ratio']:g}*{spec.cl_pf:g}p = {cc * 1e12:.3g} pF")

    # 2. Tail current from slew rate
    i5 = k["sr_v_per_us"] * 1e6 * cc
    steps.append(f"I5 = SR*Cc = {k['sr_v_per_us']:g} V/us * {cc * 1e12:.3g} pF = {i5 * 1e6:.3g} uA")

    # 3. Load mirror from the maximum input common mode
    head3 = vdd - k["vin_max"] - p.vthp + p.vthn
    if head3 <= 0.05:
        warn.append(f"vin_max={k['vin_max']} V leaves only {head3:.3f} V for M3; lower vin_max or expect M3/M1 headroom problems")
        head3 = 0.05
    s3 = i5 / (p.kp * head3**2)
    steps.append(f"S3 = I5/(kp*(VDD-Vin,max-|Vthp|+Vthn)^2) = {s3:.3g}  (headroom {head3:.3f} V)")

    # 4. Input pair from GBW
    gm1 = TWO_PI * gbw * cc
    s1 = gm1**2 / (p.kn * i5)
    steps.append(f"gm1 = 2*pi*GBW*Cc = {gm1 * 1e6:.4g} uS;  S1 = gm1^2/(kn*I5) = {s1:.3g}")

    # 5. Tail device from the minimum input common mode
    vov1 = math.sqrt(i5 / (p.kn * s1))
    vds5 = k["vin_min"] - vov1 - p.vthn
    if vds5 <= 0.05:
        warn.append(f"vin_min={k['vin_min']} V leaves only {vds5:.3f} V across M5; raise vin_min, lower I5 or widen M1")
        vds5 = 0.05
    s5 = 2 * i5 / (p.kn * vds5**2)
    steps.append(f"VDS5,sat = Vin,min - sqrt(I5/(kn*S1)) - Vthn = {vds5:.3f} V;  S5 = 2*I5/(kn*VDS5^2) = {s5:.3g}")

    # 6. Second stage from the non-dominant pole
    gm6 = k["p2_ratio"] * TWO_PI * gbw * cl
    gm4 = math.sqrt(2 * p.kp * s3 * i5 / 2)
    s6 = s3 * gm6 / gm4
    i6 = gm6**2 / (2 * p.kp * s6)
    steps.append(f"gm6 = p2_ratio*2*pi*GBW*CL = {gm6 * 1e6:.4g} uS;  S6 = S4*gm6/gm4 = {s6:.3g};  I6 = gm6^2/(2*kp*S6) = {i6 * 1e6:.3g} uA")

    # 7. Second-stage sink mirrors M8 (= M5)
    s7 = s5 * i6 / i5
    steps.append(f"S7 = S5*I6/I5 = {s7:.3g}")

    # 8. Nulling resistor: rz_ratio = 1 moves the RHP zero to infinity, > 1 makes it a LHP zero
    rz = k["rz_ratio"] / gm6
    z_lhp = 1 / (TWO_PI * cc * (rz - 1 / gm6)) if k["rz_ratio"] > 1 else math.inf
    if k["rz_ratio"] > 1:
        steps.append(f"Rz = rz_ratio/gm6 = {rz / 1e3:.3g} kOhm;  LHP zero z = 1/(2*pi*Cc*(Rz-1/gm6)) = {z_lhp / 1e6:.3g} MHz")
    else:
        steps.append(f"Rz = 1/gm6 = {rz / 1e3:.3g} kOhm (RHP zero moved to infinity)")

    sizes = {
        "w1": s1 * l1, "l1": l1, "w3": s3 * l3, "l3": l3, "w5": s5 * l5, "l5": l5,
        "w6": s6 * l6, "l6": l6, "w7": s7 * l5, "ibias": i5 * 1e6, "cc": cc * 1e12, "rz": rz / 1e3,
    }
    for name, value in list(sizes.items()):
        _, lo, hi, _ = PARAMS[name]
        if not lo <= value <= hi:
            clipped = min(max(value, lo), hi)
            warn.append(f"{name}={value:.4g} is outside [{lo}, {hi}]; clipped to {clipped:g}. The prediction no longer matches the netlist.")
            sizes[name] = clipped
    sizes = {k_: float(f"{v:.4g}") for k_, v in sizes.items()}

    # Predicted performance
    gds2 = p.lam_n(l1) * i5 / 2
    gds4 = p.lam_p(l3) * i5 / 2
    gds6 = p.lam_p(l6) * i6
    gds7 = p.lam_n(l5) * i6
    av = (gm1 / (gds2 + gds4)) * (gm6 / (gds6 + gds7))
    gbw_pred = gm1 / (TWO_PI * cc)
    p2 = gm6 / (TWO_PI * cl)
    pm = 90 - math.degrees(math.atan(gbw_pred / p2)) + math.degrees(math.atan(gbw_pred / z_lhp))
    power = vdd * (i5 + i5 + i6)  # bias branch + tail + second stage
    steps.append(f"Av = gm1/(gds2+gds4) * gm6/(gds6+gds7) = {20 * math.log10(av):.1f} dB")
    steps.append(f"GBW = gm1/(2*pi*Cc) = {gbw_pred / 1e6:.3g} MHz;  p2 = gm6/(2*pi*CL) = {p2 / 1e6:.3g} MHz;  PM = 90 - atan(GBW/p2) + atan(GBW/z) = {pm:.1f} deg")
    steps.append(f"P = VDD*(Ibias + I5 + I6) = {power * 1e3:.3g} mW")

    predicted = {
        "gain_db": round(20 * math.log10(av), 2),
        "gbw_mhz": round(gbw_pred / 1e6, 3),
        "phase_margin_deg": round(pm, 2),
        "power_mw": round(power * 1e3, 4),
    }
    return EquationResult(sizes=sizes, predicted=predicted, steps=steps, warnings=warn)


def extract(sim: dict, sizes: dict[str, float]) -> dict[str, float]:
    """Back-annotation: process parameters as the simulated transistors actually behaved.

    k is extracted from gm (k = gm^2 / (2*S*Id)) because gm is what the design equations
    rely on; in moderate inversion it differs from the classic Id-Vov fit. Only devices in
    saturation are used; parameters with no valid source are left out.
    """
    d = sim["devices"]
    sat = lambda m: d[m]["region"] == "saturation"  # noqa: E731  triode devices would corrupt the fit

    def k_eff(m: str, w: float, l: float) -> float | None:
        if not sat(m):
            return None
        gm, idd = d[m]["gm_uS"], d[m]["id_uA"]
        if not gm or not idd:
            return None
        return (gm * 1e-6) ** 2 / (2 * (w / l) * idd * 1e-6)

    def lam_l(m: str, l: float) -> float | None:
        if not sat(m):
            return None
        gds, idd = d[m].get("gds_uS"), d[m]["id_uA"]
        if not gds or not idd:
            return None
        return gds / idd * l

    def avg(*xs):
        xs = [x for x in xs if x is not None]
        return sum(xs) / len(xs) if xs else None

    s = sizes
    out = {
        "kn": avg(k_eff("m1", s["w1"], s["l1"]), k_eff("m2", s["w1"], s["l1"])),
        "kp": k_eff("m6", s["w6"], s["l6"]),
        "vthn": d["m1"].get("vth_V") if sat("m1") else None,
        "vthp": abs(d["m3"]["vth_V"]) if sat("m3") and d["m3"].get("vth_V") is not None else None,
        "lam_n_um": avg(lam_l("m2", s["l1"]), lam_l("m7", s["l5"])),
        "lam_p_um": avg(lam_l("m4", s["l3"]), lam_l("m6", s["l6"])),
    }
    return {k_: float(f"{v:.4g}") for k_, v in out.items() if v is not None}
