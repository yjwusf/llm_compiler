# Peripherals

## External Data Source

The primary source of model data is Ethernet, not off-chip DRAM. Input tokens,
activation streams, weights, control packets, or model fragments that come from
outside the accelerator must enter through a digital Ethernet path unless a
future L1 document explicitly adds another source.

Off-chip DRAM is not part of the initial external data-source contract. On-chip
SRAM remains part of the architecture as a configurable buffering and staging
resource.

## RGMII Boundary

The required Ethernet physical interface is RGMII. Generated or handwritten RTL
terminates at a digital RGMII MAC-side boundary and connects to an external
Ethernet PHY.

The RTL boundary is digital-only:

- No analog PHY implementation is generated in this repository.
- No mixed-signal macros are required by the architecture contract.
- No ADC, DAC, analog PLL, serializer/deserializer macro, or analog pad model
  is part of the generated design.
- FPGA and ASIC/OpenROAD targets may provide digital timing constraints,
  clocking constraints, IO placement hints, and wrappers for the RGMII pins.

The external PHY is responsible for analog Ethernet signaling. This repository
is responsible for digital RGMII receive pins for initial ingress, optional
digital RGMII transmit pins when a future outbound path is documented, packet
framing, data stream conversion, and integration into the accelerator data path.

## Ingress Data Path

The initial required data path is:

1. External Ethernet PHY.
2. Digital RGMII pins.
3. RGMII Ethernet ingress module.
4. Packet or stream decoder.
5. On-chip SRAM staging.
6. Systolic-array dataflow.

No compiler pass may assume off-chip DRAM as the source of input model data.
Memory planning may allocate on-chip SRAM buffers for Ethernet-ingested data.

## Architecture JSON

Architecture JSON must describe Ethernet ingress when external data is present.
The expected shape is:

```json
{
  "io": {
    "external_data_source": {
      "kind": "ethernet",
      "mac_interface": "rgmii",
      "phy_boundary": "external",
      "digital_only": true,
      "stream_data_width": 64,
      "fifo_depth": 1024,
      "enable_frame_check": true
    }
  }
}
```

Compiler defaults for omitted Ethernet fields must be documented in L2 before
they are implemented.

## Verification

RGMII-related modules must have module-local VIPs that drive only the digital
RGMII boundary and internal stream boundary. Tests must not require an analog or
mixed-signal simulation model.
