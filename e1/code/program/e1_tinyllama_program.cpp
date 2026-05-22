#include "e1_device_mmio.hpp"

#include <cstdint>

namespace {

struct TileCommand {
  std::uint32_t input_addr;
  std::uint32_t weight_addr;
  std::uint32_t output_addr;
  std::uint16_t rows;
  std::uint16_t cols;
  std::uint16_t depth;
};

void run_systolic_tile(const TileCommand& tile) {
  using namespace e1_device;

  wait_for_array_idle();

  write32(kArrayInputAddr, tile.input_addr);
  write32(kArrayWeightAddr, tile.weight_addr);
  write32(kArrayOutputAddr, tile.output_addr);
  write32(kArrayRows, tile.rows);
  write32(kArrayCols, tile.cols);
  write32(kArrayDepth, tile.depth);
  write32(kArrayStart, kArrayStartRun);

  wait_for_array_idle();
}

}  // namespace

extern "C" void e1_main() {
  constexpr TileCommand first_attention_tile{
      0x00010000u,
      0x00040000u,
      0x00080000u,
      16,
      16,
      16,
  };

  run_systolic_tile(first_attention_tile);
}
