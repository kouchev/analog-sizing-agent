"""The agent loop, written by hand so every step is visible.

    goal -> Claude thinks -> calls simulate_ota -> reads the result -> thinks again -> ... -> final answer
"""

from __future__ import annotations

import anthropic

from .circuit import PARAMS, Process, Spec

MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are an experienced analog IC designer. Size a two-stage Miller-compensated OTA using the simulate_ota tool.

Process: {process}. Minimum channel length {lmin} um.

Topology:
- NMOS input pair M1/M2 (M2 gate = non-inverting input, M1 gate = inverting input), PMOS current-mirror load M3/M4.
- NMOS tail source M5, biased by diode-connected M8 fed from the reference current ibias (M5 and M8 have the same size).
- Second stage: PMOS common-source M6 driven from the first-stage output, NMOS current sink M7 mirrored from M8 (same length as M5/M8).
- Miller capacitor cc in series with nulling resistor rz between the first-stage output and the output; load CL.
- The testbench closes the loop at DC only, so the output sits at the input common mode and the AC result is open loop.

Spec: {spec}. All transistors must be in saturation.
Secondary goal: once the spec is met, lower power if you can do so without losing margin.

You have a budget of {max_sims} simulations. Use hand analysis (gm/Id, GBW = gm1/Cc, non-dominant pole near gm6/CL, RHP zero and rz) to choose each step, and change several parameters at once when your reasoning supports it. Parameters:
{params}

When the spec is met with margin, or the budget is nearly used, stop calling the tool and give a final answer with: the parameter table, the simulated metrics, and the two or three trade-offs that mattered most."""


def build_system_prompt(spec: Spec, proc: Process, max_sims: int) -> str:
    params = "\n".join(f"- {k} [{u}], {lo}-{hi}: {d}" for k, (u, lo, hi, d) in PARAMS.items())
    return SYSTEM_PROMPT.format(process=proc.name, lmin=proc.lmin_um, spec=spec.describe(),
                                max_sims=max_sims, params=params)


def run_agent(
    session,
    client: anthropic.Anthropic | None = None,
    model: str = MODEL,
    effort: str = "high",
    max_turns: int = 40,
    verbose: bool = True,
) -> str:
    """Run the loop until Claude gives a final answer. Returns the final text.

    `session` supplies the tools and executes them: SizingSession (free sizing) or
    EquationSession (equation-first methodology).
    """
    client = client or anthropic.Anthropic()
    say = print if verbose else (lambda *a, **k: None)
    system = session.system_prompt()
    messages: list[dict] = [{"role": "user", "content": session.first_message}]
    session.log("start", model=model, effort=effort, system=system)
    usage = {"input_tokens": 0, "output_tokens": 0}
    final_text = ""

    for turn in range(1, max_turns + 1):
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            tools=session.tools,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},  # show a summary of the reasoning
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",  # on a policy decline, retry on Anthropic's recommended model
        )
        usage["input_tokens"] += response.usage.input_tokens
        usage["output_tokens"] += response.usage.output_tokens

        for block in response.content:
            if block.type == "thinking" and block.thinking:
                say(f"\n[thinking] {block.thinking.strip()}")
            elif block.type == "text" and block.text.strip():
                say(f"\n[claude] {block.text.strip()}")
                final_text = block.text
            elif block.type == "fallback":
                say(f"\n[note] {block.from_.model} declined; {block.to.model} continued")

        if response.stop_reason == "refusal":
            say("\n[stop] The request was declined.")
            break
        if response.stop_reason == "max_tokens":
            say("\n[stop] Hit max_tokens in a single turn; raise max_tokens or lower effort.")
            break

        # Keep the assistant turn exactly as returned (thinking blocks included).
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "pause_turn":
            continue
        if response.stop_reason != "tool_use":
            break  # end_turn: Claude has given its final answer

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            content, is_error = session.run_tool(block.name, block.input)
            say("\n" + session.console_line(block.name, block.input, content, is_error))
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error})
        messages.append({"role": "user", "content": tool_results})
    else:
        say(f"\n[stop] Reached max_turns={max_turns}.")

    session.log("end", usage=usage, final_text=final_text)
    say(f"\n[usage] input tokens {usage['input_tokens']:,}, output tokens {usage['output_tokens']:,}")
    return final_text
