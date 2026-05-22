#include "e1_chip_model.hpp"

#include <cstdint>
#include <iostream>
#include <vector>

namespace {

e1::SystolicCommand first_attention_tile() {
  return {
      0x00010000u,
      0x00040000u,
      0x00080000u,
      16,
      16,
      16,
  };
}

}  // namespace

int main() {
  e1::ChipModel chip;
  chip.reset();

  const std::vector<std::uint8_t> ethernet_payload = {
      0x10, 0x11, 0x12, 0x13,
      0x20, 0x21, 0x22, 0x23,
  };
  chip.load_ethernet_payload(ethernet_payload);
  chip.write_array_command(first_attention_tile());
  chip.run_until_idle(1024);

  const e1::PerfCounters& counters = chip.counters();
  const bool pass =
      counters.cycles > 0 &&
      counters.instructions == 1 &&
      counters.array_commands == 1 &&
      counters.output_transfers == 1 &&
      counters.error_events == 0;

  std::cout
      << "{\n"
      << "  \"schema\": \"e1-chip-model-smoke-v0\",\n"
      << "  \"status\": \"" << (pass ? "pass" : "fail") << "\",\n"
      << "  \"program\": \"first_attention_tile\",\n"
      << "  \"counters\": {\n"
      << "    \"cycles\": " << counters.cycles << ",\n"
      << "    \"instructions\": " << counters.instructions << ",\n"
      << "    \"input_transfers\": " << counters.input_transfers << ",\n"
      << "    \"output_transfers\": " << counters.output_transfers << ",\n"
      << "    \"stall_cycles\": " << counters.stall_cycles << ",\n"
      << "    \"error_events\": " << counters.error_events << ",\n"
      << "    \"array_commands\": " << counters.array_commands << ",\n"
      << "    \"rgmii_rx_cycles\": " << counters.rgmii_rx_cycles << ",\n"
      << "    \"frames_seen\": " << counters.frames_seen << "\n"
      << "  }\n"
      << "}\n";

  return pass ? 0 : 1;
}
