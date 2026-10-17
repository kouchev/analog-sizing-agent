"""Draw the two-stage OTA with the sizes of one design, as an SVG you can open and edit in Inkscape.

    python scripts/draw_schematic.py docs/runs/2026-10-15-sg13g2-D17.json -o docs/schematic_D17.svg

The JSON holds "sizes" (circuit.PARAMS units) and optionally "fingers", "devices" (simulated
operating points), "metrics", "process" and "design_id"; see docs/runs/*-D17.json.
Requires: pip install schemdraw
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import schemdraw
import schemdraw.elements as elm

VDD_Y, GND_Y = 10.0, 2.0


def fet(d, kind: str, x: float, top: float, gate_left: bool):
    """Place a transistor whose top terminal (NMOS drain, PMOS source) sits at (x, top)."""
    e = (elm.NFet if kind == "n" else elm.PFet)(bulk=False).theta(0)  # fixed orientation; otherwise it follows the last wire
    if gate_left:
        e = e.reverse()
    return d.add(e.at((x, top)))


def label(d, xy, text, loc="right", size=9, color="black"):
    halign = {"right": "left", "left": "right", "center": "center"}[loc]
    d.add(elm.Label().at(xy).label(text, fontsize=size, halign=halign, valign="center", color=color))


def wire(d, *pts):
    for a, b in zip(pts, pts[1:]):
        d.add(elm.Line().at(a).to(b))


def draw(design: dict, out: Path) -> None:
    s = design["sizes"]
    ng = design.get("fingers", {})
    ops = design.get("devices", {})

    def dev_text(m, w, l):
        lines = [m.upper(), f"W/L = {s[w]:g}/{s[l]:g} µm"]
        if ng.get(m, 1) > 1:
            lines.append(f"ng = {ng[m]}")
        if m in ops:
            lines.append(f"Id = {ops[m]['id_uA']:.3g} µA, gm/Id = {ops[m]['gm_over_id']:.3g}")
        return "\n".join(lines)

    schemdraw.config(fontsize=10, lw=1.4)
    with schemdraw.Drawing(show=False) as d:
        # Rails
        wire(d, (0, VDD_Y), (18.5, VDD_Y))
        wire(d, (0, GND_Y), (18.5, GND_Y))
        label(d, (-0.3, VDD_Y), "VDD", loc="left", size=11)
        d.add(elm.Ground().at((9.25, GND_Y)))

        # Bias branch: Ibias into diode-connected M8
        d.add(elm.SourceI().at((0, VDD_Y)).down().to((0, 6.0)).label(f"Ibias\n{s['ibias']:g} µA", loc="bottom", ofst=0.3))
        wire(d, (0, 6.0), (0, 3.5))
        m8 = fet(d, "n", 0, 3.5, gate_left=False)
        wire(d, m8.gate, (m8.gate[0], 3.5), (0, 3.5))
        d.add(elm.Dot().at((m8.gate[0], 3.5)))
        label(d, (-0.4, 2.75), dev_text("m8", "w5", "l5"), loc="left", size=8)

        # First stage: PMOS mirror M3 (diode) / M4, NMOS pair M1 / M2, tail M5
        m3 = fet(d, "p", 4, VDD_Y, gate_left=False)
        m4 = fet(d, "p", 8, VDD_Y, gate_left=True)
        wire(d, m3.gate, m4.gate)
        mid_g = ((m3.gate[0] + m4.gate[0]) / 2, m3.gate[1])
        d.add(elm.Dot().at(mid_g))
        wire(d, mid_g, (mid_g[0], 8.5), m3.drain)
        label(d, (3.6, 9.25), dev_text("m3", "w3", "l3"), loc="left", size=8)
        label(d, (8.4, 9.25), dev_text("m4", "w3", "l3"), loc="right", size=8)

        m1 = fet(d, "n", 4, 7.0, gate_left=True)
        m2 = fet(d, "n", 8, 7.0, gate_left=False)
        wire(d, m3.drain, m1.drain)
        wire(d, m4.drain, m2.drain)
        label(d, (m1.gate[0] - 0.2, m1.gate[1]), "in−", loc="left", size=11)
        label(d, (m2.gate[0] + 0.2, m2.gate[1]), "in+", loc="right", size=11)
        label(d, (3.4, 4.6), dev_text("m1", "w1", "l1"), loc="left", size=8)
        label(d, (8.6, 4.6), dev_text("m2", "w1", "l1"), loc="right", size=8)

        wire(d, m1.source, m2.source)
        d.add(elm.Dot().at((6, 5.5)))
        wire(d, (6, 5.5), (6, 3.5))
        m5 = fet(d, "n", 6, 3.5, gate_left=True)
        wire(d, m8.gate, m5.gate)
        label(d, (3.0, 2.95), "nb", loc="center", size=9, color="#555")
        label(d, (6.4, 2.75), dev_text("m5", "w5", "l5"), loc="right", size=8)

        # Second stage: PMOS common source M6, NMOS sink M7 (gate on nb), compensation Rz + Cc
        x2 = (8, 7.75)
        d.add(elm.Dot().at(x2))
        m6 = fet(d, "p", 14, VDD_Y, gate_left=True)
        wire(d, x2, (11.5, 7.75), (11.5, m6.gate[1]), m6.gate)
        d.add(elm.Dot().at((11.5, 7.75)))
        label(d, (14.4, 9.25), dev_text("m6", "w6", "l6"), loc="right", size=8)

        m7 = fet(d, "n", 14, 3.5, gate_left=True)
        label(d, (m7.gate[0] - 0.2, m7.gate[1]), "nb", loc="left", size=9, color="#555")
        label(d, (14.4, 2.75), dev_text("m7", "w7", "l5"), loc="right", size=8)

        wire(d, m6.drain, m7.drain)
        out_node = (14, 6.0)
        d.add(elm.Dot().at(out_node))
        d.add(elm.Resistor().at((11.5, 7.75)).down().length(1.75).label(f"Rz\n{s['rz']:g} kΩ", loc="bottom", ofst=0.15))
        d.add(elm.Capacitor().at((11.5, 6.0)).right().to(out_node).label(f"Cc  {s['cc']:g} pF", loc="bottom", ofst=0.2))
        wire(d, out_node, (18.5, 6.0))
        d.add(elm.Dot(open=True).at((18.5, 6.0)).label("out", loc="right", fontsize=11))
        cl_pf = design.get("cl_pf", 2.0)
        d.add(elm.Capacitor().at((17.5, 6.0)).down().to((17.5, GND_Y)).label(f"CL\n{cl_pf:g} pF", loc="bottom", ofst=0.2))
        d.add(elm.Dot().at((17.5, 6.0)))
        label(d, (7.6, 7.95), "x2", loc="left", size=9, color="#555")

        # Title block
        title = [f"Two-stage Miller OTA · {design.get('design_id', '')} · {design.get('process', '')}"]
        m = design.get("metrics")
        if m:
            title.append(f"Simulated: gain {m['gain_db']:.1f} dB · GBW {m['gbw_mhz']:.1f} MHz · "
                         f"PM {m['phase_margin_deg']:.1f}° · P {m['power_mw']:.3g} mW · VDD 1.2 V")
        label(d, (-4.5, 11.6), "\n".join(title), loc="right", size=10)
        d.save(str(out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("design_json")
    ap.add_argument("-o", "--out", default="schematic.svg")
    args = ap.parse_args()
    design = json.loads(Path(args.design_json).read_text())
    draw(design, Path(args.out))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
