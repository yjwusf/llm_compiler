#include "e1_tinyllama_full_schedule.hpp"

#include <cstdint>
#include <iostream>

int main() {
  using namespace e1_device::tinyllama_full;

  constexpr std::uint32_t kCyclesPerTileCommand = 8u;
  constexpr std::uint64_t kExpectedTotalCycles = 30277632ull;
  const std::uint64_t total_cycles = total_tile_commands() * kCyclesPerTileCommand;
  const bool pass =
      total_tile_commands() == 3784704ull &&
      total_cycles == kExpectedTotalCycles &&
      kTileRows == 16u &&
      kTileCols == 16u &&
      kTileDepth == 16u;

  std::cout
      << "{\n"
      << "  \"schema\": \"e1-full-checkpoint-rtl-cycle-smoke-v0\",\n"
      << "  \"status\": \"" << (pass ? "pass" : "fail") << "\",\n"
      << "  \"cycles_per_tile_command\": " << kCyclesPerTileCommand << ",\n"
      << "  \"total_tile_commands\": " << total_tile_commands() << ",\n"
      << "  \"total_rtl_cycles\": " << total_cycles << "\n"
      << "}\n";

  return pass ? 0 : 1;
}
