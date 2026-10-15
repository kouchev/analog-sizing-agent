import shutil

import pytest

from sizing_agent.circuit import DEFAULT_DESIGN, Process, Spec, validate
from sizing_agent.simulator import simulate

needs_ngspice = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")


def test_validate_flags_out_of_range_and_unknown():
    bad = {**DEFAULT_DESIGN, "l1": 0.01, "foo": 1.0}
    errors = validate(bad)
    assert any("l1" in e for e in errors)
    assert any("foo" in e for e in errors)
    assert validate(DEFAULT_DESIGN) == []


@needs_ngspice
def test_default_design_simulates():
    r = simulate(DEFAULT_DESIGN, Spec(), Process())
    m = r["metrics"]
    assert 40 < m["gain_db"] < 100
    assert 1 < m["gbw_mhz"] < 1000
    assert 0 < m["phase_margin_deg"] < 120
    assert 0 < m["power_mw"] < 5
    assert set(r["devices"]) == {f"m{i}" for i in range(1, 9)}


@needs_ngspice
def test_more_cc_lowers_gbw():
    lo = simulate({**DEFAULT_DESIGN, "cc": 0.5}, Spec(), Process())["metrics"]["gbw_mhz"]
    hi = simulate({**DEFAULT_DESIGN, "cc": 2.0}, Spec(), Process())["metrics"]["gbw_mhz"]
    assert lo > hi
