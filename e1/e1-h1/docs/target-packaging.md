# E1-H1 Target Packaging

E1-H1 emits smoke packages for FPGA and ASIC/OpenROAD targets from the same
generated SoC top and IP RTL files listed in the IP manifests. These packages
are not full implementation flows yet; they are the first target artifacts that
prove the generated top, mock IPs, digital RGMII boundary, and target
constraints are wired together.

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
- `e1/e1-h1/generated/implementation_matrix.json`
- `e1/e1-h1/generated/flists/active.f`
- `e1/e1-h1/generated/flists/imp1/*.f`

## Contract

- Both targets use `e1_h1_soc_top` as the top.
- Both targets consume `e1/e1-h1/generated/e1_h1_soc_top.sv`.
- Both targets include every current E1-H1 mock IP RTL file through the `rtl`
  field in `e1/e1-h1/ip/*.json`.
- The active target filelists must match the active implementation flist. Today
  active is `imp1`, the accepted mock implementation set.
- `imp2` implementation flists are emitted only after candidate RTL passes the
  Verilator+DPI VIP equivalence gate against `imp1`.
- Shared implementation RTL, such as `e1_h1_config_sram.sv`, appears only once
  in target filelists even when multiple IP manifests use it.
- RGMII remains digital-only. The external Ethernet PHY owns analog signaling.
- OpenROAD packaging must not require an analog PHY macro or off-chip DRAM data
  source.

The E1-H1 test suite regenerates and checks these package files.
It also Verilates and runs the generated `e1_h1_soc_top` with the target
manifest RTL filelist, drives the digital RGMII receive pins, and checks that
the integrated CPU-to-array path reaches array-busy and halted debug states.
