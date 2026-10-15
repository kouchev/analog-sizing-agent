"""Runs only when the SG13G2 setup is present (PDK_ROOT and NGSPICE pointing at ngspice >= 45)."""

import os
from pathlib import Path

import pytest

from sizing_agent.circuit import Spec, sg13g2
from sizing_agent.equations import DEFAULT_KNOBS, ProcessParams, design
from sizing_agent.simulator import simulate

osdi = Path(os.environ.get("PDK_ROOT", "/nonexistent")) / "ihp-sg13g2/libs.tech/ngspice/osdi/psp103.osdi"
pytestmark = pytest.mark.skipif(not osdi.exists() or "NGSPICE" not in os.environ, reason="SG13G2 setup not found")


def test_sg13g2_equation_design_simulates_in_saturation():
    # process parameters as back-annotated from SG13G2 (typical corner)
    p = ProcessParams(kn=2.25e-4, kp=6.9e-5, vthn=0.323, vthp=0.364, lam_n_um=0.235, lam_p_um=0.078)
    r = design(Spec(), DEFAULT_KNOBS, p)
    sim = simulate(r.sizes, Spec(), sg13g2())
    assert sim["spec_check"]["all_saturated"]
    assert abs(sim["metrics"]["gain_db"] - r.predicted["gain_db"]) < 3
    assert abs(sim["metrics"]["phase_margin_deg"] - r.predicted["phase_margin_deg"]) < 5


def test_wide_devices_are_fingered():
    proc = sg13g2()
    assert proc.device("m6", "out", "x2", "vdd", "vdd", "p", 44.0, 0.5).endswith("ng=9")
    assert "ng=" not in proc.device("m1", "x1", "inn", "tail", "0", "n", 2.0, 0.5)
