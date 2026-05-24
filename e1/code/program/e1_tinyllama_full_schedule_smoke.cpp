#include "e1_tinyllama_full_schedule.hpp"

#include <cstdint>
#include <iostream>

int main() {
  using namespace e1_device::tinyllama_full;

  const TileCommand first = command_for(0, 0, 0, 0);
  const TileCommand last = command_for(
      kLayerCount - 1,
      kLinearOpCount - 1,
      kLinearOps[kLinearOpCount - 1].input_tiles - 1,
      kLinearOps[kLinearOpCount - 1].output_tiles - 1);

  const bool pass =
      kLayerCount == 22u &&
      kLinearOpCount == 7u &&
      commands_per_layer() == 172032ull &&
      total_tile_commands() == 3784704ull &&
      kLinearOps[0].input_tiles == 128u &&
      kLinearOps[0].output_tiles == 128u &&
      kLinearOps[6].input_tiles == 352u &&
      kLinearOps[6].output_tiles == 128u &&
      first.input_addr == kInputBase &&
      first.weight_addr == kWeightBase &&
      first.output_addr == kOutputBase &&
      first.rows == kTileRows &&
      first.cols == kTileCols &&
      first.depth == kTileDepth &&
      last.rows == kTileRows &&
      last.cols == kTileCols &&
      last.depth == kTileDepth;

  std::cout
      << "{\n"
      << "  \"schema\": \"e1-full-checkpoint-command-stream-smoke-v0\",\n"
      << "  \"status\": \"" << (pass ? "pass" : "fail") << "\",\n"
      << "  \"layers\": " << kLayerCount << ",\n"
      << "  \"linear_ops_per_layer\": " << kLinearOpCount << ",\n"
      << "  \"commands_per_layer\": " << commands_per_layer() << ",\n"
      << "  \"total_tile_commands\": " << total_tile_commands() << ",\n"
      << "  \"first_input_addr\": " << first.input_addr << ",\n"
      << "  \"last_output_addr\": " << last.output_addr << "\n"
      << "}\n";

  return pass ? 0 : 1;
}
