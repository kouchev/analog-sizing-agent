# Moving to IHP SG13G2

The starter uses generic BSIM4 models (`models/generic130.lib`) so it runs anywhere with a stock ngspice. To size on a real open-source process, point the `Process` at the IHP SG13G2 PDK.

> Not yet tested in this repo. Treat these steps as the plan for the next milestone.

1. Install the PDK: clone [IHP-Open-PDK](https://github.com/IHP-GmbH/IHP-Open-PDK) and set `PDK_ROOT`.
2. SG13G2 MOSFETs use PSP models compiled with OpenVAF. Make sure ngspice loads the OSDI file, e.g. in `~/.spiceinit`:
   ```
   osdi <PDK_ROOT>/ihp-sg13g2/libs.tech/ngspice/osdi/psp103_nqs.osdi
   ```
3. The devices are subcircuits (`sg13_lv_nmos`, `sg13_lv_pmos`), so the instance prefix changes from `M` to `X`, and operating-point names change accordingly (for example `@n.x1.nsg13_lv_nmos[gm]`). Update `netlist()` and the `OP` parsing in `simulator.py`.
4. Create a process object:
   ```python
   Process(
       name="IHP SG13G2 low-voltage MOS, typical corner",
       include=".lib <PDK_ROOT>/ihp-sg13g2/libs.tech/ngspice/models/cornerMOSlv.lib mos_tt",
       nmos="sg13_lv_nmos",
       pmos="sg13_lv_pmos",
       lmin_um=0.13,
   )
   ```
5. Re-run `python -m sizing_agent --check`, then the agent.

Good follow-ups: corner simulations (`mos_ff`, `mos_ss`) as a second tool, and Monte Carlo for offset.
