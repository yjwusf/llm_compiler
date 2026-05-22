# E1-H1 Target Packaging

E1-H1 emits smoke packages for FPGA and ASIC/OpenROAD targets from the same
generated SoC top and IP RTL files. These packages are not full implementation
flows yet; they are the first target artifacts that prove the generated top,
mock IPs, digital RGMII boundary, and target constraints are wired together.

## Generator

Run:

```sh
python3 e1/tools/run_e1_pipeline.py --clean
```

The pipeline writes:

- `e1/e1-h1/generated/targets/manifest.json`
- `e1/e1-h1/generated/targets/fpga/rtl.filelist`
- `e1/e1-h1/generated/targets/fpga/constraints.xdc`
- `e1/e1-h1/generated/targets/fpga/run_synth.tcl`
- `e1/e1-h1/generated/targets/openroad/rtl.filelist`
- `e1/e1-h1/generated/targets/openroad/constraints.sdc`
- `e1/e1-h1/generated/targets/openroad/config.mk`

## Contract

- Both targets use `e1_h1_soc_top` as the top.
- Both targets consume `e1/e1-h1/generated/e1_h1_soc_top.sv`.
- Both targets include every current E1-H1 mock IP RTL file.
- RGMII remains digital-only. The external Ethernet PHY owns analog signaling.
- OpenROAD packaging must not require an analog PHY macro or off-chip DRAM data
  source.

The E1-H1 test suite regenerates and checks these package files.
