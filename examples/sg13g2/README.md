# Running on IHP SG13G2

Tested with IHP-Open-PDK (commit 5e6d592, Sept 2026), OpenVAF-Reloaded built with LLVM 18, and ngspice 45.2.

## Setup

```bash
./scripts/bootstrap.sh               # venv + toolchain into .tools/; 15-20 minutes the first time
source .venv/bin/activate            # sets PDK_ROOT and NGSPICE
pytest -q                            # the two SG13G2 tests now run instead of being skipped
```

To install the toolchain somewhere else: `PREFIX=~/eda ./scripts/setup_sg13g2.sh`, then set `PDK_ROOT=~/eda/IHP-Open-PDK` and `NGSPICE=~/eda/ngspice/bin/ngspice`.

Why each piece is needed:

- **PSP103 as OSDI.** SG13G2 MOSFETs use the PSP103 Verilog-A model. OpenVAF-Reloaded compiles it to `psp103.osdi`, which ngspice loads through `.spiceinit` (the simulator wrapper writes that file for you).
- **ngspice 45 or newer.** OpenVAF-Reloaded writes OSDI v0.4. Ubuntu's ngspice 42 only loads v0.3 and fails with "NGSPICE only supports OSDI v0.3".
- The warnings about `pspnqs103va` refer to the RF variant of the model and can be ignored.

## Use

```bash
python -m sizing_agent --process sg13g2 --check          # scripted equations/simulate/recalibrate loop, no API
python -m sizing_agent --process sg13g2                  # the agent with the equation-first methodology
python -m sizing_agent --process sg13g2 --mode free      # the agent picks sizes directly
```

## Things the PDK taught us

- **Wide single fingers are invalid.** A 69 µm single-finger PMOS reports |Vth| = 0.61 V instead of 0.36 V. The netlist now splits devices into fingers of at most 5 µm (`ng=`), as a layout would.
  Before this fix, the oversized threshold of a single-finger M6 needed a much larger Vsg than M4, pulled the first-stage output down and put M2 in triode.
- **Short-channel |Vth| shift is modest.** PMOS |Vth| is 0.364 V at L = 0.5 µm, 0.383 V at 0.25 µm and 0.412 V at 0.13 µm (W = 5 µm). The procedure still assumes M6 and M4 have the same Vgs, so `l6_um = l3_um` is the safe default.
- **Back-annotation must ignore devices in triode.** Their gds is huge and corrupts λ.
