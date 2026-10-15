import math
import shutil

import pytest

from sizing_agent.circuit import Spec, generic130
from sizing_agent.eq_tools import EquationSession
from sizing_agent.equations import DEFAULT_KNOBS, ProcessParams, design, validate_knobs

needs_ngspice = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")


def test_hand_calculation_matches():
    """Check the procedure against values worked out by hand for the default knobs."""
    spec, p = Spec(), ProcessParams()
    r = design(spec, DEFAULT_KNOBS, p)
    cc = 0.25 * 2e-12
    i5 = 60e6 * cc                                   # 30 uA
    gm1 = 2 * math.pi * 50e6 * cc                    # 157 uS
    assert r.sizes["cc"] == pytest.approx(0.5)
    assert r.sizes["ibias"] == pytest.approx(i5 * 1e6, rel=1e-3)
    assert r.sizes["w1"] == pytest.approx(gm1**2 / (p.kn * i5) * 0.5, rel=1e-3)
    assert r.predicted["gbw_mhz"] == pytest.approx(50.0, rel=1e-3)
    assert r.predicted["phase_margin_deg"] == pytest.approx(90 - math.degrees(math.atan(1 / 2.5)), abs=0.01)


def test_rz_ratio_adds_lhp_zero_phase():
    spec, p = Spec(), ProcessParams()
    base = design(spec, DEFAULT_KNOBS, p)
    lhp = design(spec, {**DEFAULT_KNOBS, "rz_ratio": 2.0}, p)
    assert lhp.sizes["rz"] == pytest.approx(2 * base.sizes["rz"], rel=1e-3)
    cc = 0.25 * 2e-12
    gm6 = 2.5 * 2 * math.pi * 50e6 * 2e-12
    z = 1 / (2 * math.pi * cc * (2 / gm6 - 1 / gm6))
    expected = base.predicted["phase_margin_deg"] + math.degrees(math.atan(50e6 / z))
    assert lhp.predicted["phase_margin_deg"] == pytest.approx(expected, abs=0.02)


def test_knob_validation():
    assert validate_knobs(DEFAULT_KNOBS) == []
    errs = validate_knobs({**DEFAULT_KNOBS, "cc_ratio": 5, "bogus": 1})
    assert any("cc_ratio" in e for e in errs) and any("bogus" in e for e in errs)


def test_simulate_only_accepts_calculated_designs(tmp_path):
    s = EquationSession(Spec(), generic130(), max_sims=3, log_dir=tmp_path)
    content, err = s.run_tool("simulate_design", {"design_id": "D99"})
    assert err and "Only designs from design_from_equations" in content
    s.close()


@needs_ngspice
def test_full_pass_and_recalibration(tmp_path):
    s = EquationSession(Spec(), generic130(), max_sims=3, log_dir=tmp_path)
    _, err = s.run_tool("design_from_equations", {"rationale": "t", **DEFAULT_KNOBS})
    assert not err
    _, err = s.run_tool("simulate_design", {"design_id": "D1"})
    assert not err and s.sims_used == 1
    kn_before = s.pparams.kn
    content, err = s.run_tool("recalibrate_process", {"from_design_id": "D1", "parameters": ["kn"], "rationale": "t"})
    assert not err and s.pparams.kn == s.designs["D1"]["extracted"]["kn"] != kn_before
    # values must come from the simulation, not from the caller
    content, err = s.run_tool("recalibrate_process", {"from_design_id": "D2", "parameters": ["kn"], "rationale": "t"})
    assert err
    s.close()
