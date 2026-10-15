"""Exercises the agent loop with a scripted fake client: no API key or network needed."""

import shutil
from types import SimpleNamespace as NS

import pytest

from sizing_agent.agent import run_agent
from sizing_agent.circuit import DEFAULT_DESIGN, Process, Spec
from sizing_agent.tools import SizingSession

pytestmark = pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")


class FakeClient:
    def __init__(self, responses):
        self.calls = []
        self._responses = iter(responses)
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})  # snapshot: the loop keeps appending
        return next(self._responses)


def _resp(content, stop_reason):
    return NS(content=content, stop_reason=stop_reason, usage=NS(input_tokens=10, output_tokens=5))


def test_loop_runs_tool_then_finishes(tmp_path):
    tool_call = NS(type="tool_use", id="toolu_1", name="simulate_ota", input={"rationale": "baseline", **DEFAULT_DESIGN})
    client = FakeClient([
        _resp([NS(type="thinking", thinking="Start from the default."), tool_call], "tool_use"),
        _resp([NS(type="text", text="Final design: see table.")], "end_turn"),
    ])
    session = SizingSession(Spec(), Process(), max_sims=5, log_dir=tmp_path)
    final = run_agent(session, client=client, verbose=False)

    assert final == "Final design: see table."
    assert session.sims_used == 1
    # second request must carry the assistant turn and the tool result
    msgs = client.calls[1]["messages"]
    assert msgs[-2]["role"] == "assistant"
    result = msgs[-1]["content"][0]
    assert result["tool_use_id"] == "toolu_1" and result["is_error"] is False
    assert '"gain_db"' in result["content"]
    session.close()


def test_budget_guardrail(tmp_path):
    session = SizingSession(Spec(), Process(), max_sims=1, log_dir=tmp_path)
    _, err1 = session.run_tool("simulate_ota", {"rationale": "a", **DEFAULT_DESIGN})
    content, err2 = session.run_tool("simulate_ota", {"rationale": "b", **DEFAULT_DESIGN})
    assert not err1 and err2 and "budget exhausted" in content
    session.close()


def test_invalid_input_does_not_use_budget(tmp_path):
    session = SizingSession(Spec(), Process(), max_sims=3, log_dir=tmp_path)
    content, err = session.run_tool("simulate_ota", {"rationale": "x", **DEFAULT_DESIGN, "w1": 9999})
    assert err and "w1" in content and session.sims_used == 0
    session.close()
