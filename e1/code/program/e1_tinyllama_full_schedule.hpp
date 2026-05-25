#ifndef E1_CODE_PROGRAM_E1_TINYLLAMA_FULL_SCHEDULE_HPP
#define E1_CODE_PROGRAM_E1_TINYLLAMA_FULL_SCHEDULE_HPP

#include <cstdint>

namespace e1_device::tinyllama_full {

struct LinearOpPlan {
  const char* name;
  std::uint32_t input_width;
  std::uint32_t output_width;
  std::uint32_t input_tiles;
  std::uint32_t output_tiles;
};

struct TileCommand {
  std::uint32_t input_addr;
  std::uint32_t weight_addr;
  std::uint32_t output_addr;
  std::uint16_t rows;
  std::uint16_t cols;
  std::uint16_t depth;
};

constexpr std::uint32_t kLayerCount = 22u;
constexpr std::uint32_t kLinearOpCount = 7u;
constexpr std::uint16_t kTileRows = 16u;
constexpr std::uint16_t kTileCols = 16u;
constexpr std::uint16_t kTileDepth = 16u;
constexpr std::uint32_t kTileBytes = 64u;
constexpr std::uint32_t kInputBase = 0x01000000u;
constexpr std::uint32_t kWeightBase = 0x10000000u;
constexpr std::uint32_t kOutputBase = 0x30000000u;
constexpr std::uint32_t kLayerInputStride = 0x00100000u;
constexpr std::uint32_t kLayerWeightStride = 0x01000000u;
constexpr std::uint32_t kLayerOutputStride = 0x00100000u;
constexpr std::uint32_t kOpInputStride = 0x00010000u;
constexpr std::uint32_t kOpWeightStride = 0x00100000u;
constexpr std::uint32_t kOpOutputStride = 0x00010000u;
constexpr std::uint64_t kCommandDigestOffsetBasis = 1469598103934665603ull;
constexpr std::uint64_t kCommandDigestPrime = 1099511628211ull;

static constexpr LinearOpPlan kLinearOps[kLinearOpCount] = {
    {"q_proj", 2048u, 2048u, 128u, 128u},
    {"k_proj", 2048u, 256u, 128u, 16u},
    {"v_proj", 2048u, 256u, 128u, 16u},
    {"o_proj", 2048u, 2048u, 128u, 128u},
    {"gate_proj", 2048u, 5632u, 128u, 352u},
    {"up_proj", 2048u, 5632u, 128u, 352u},
    {"down_proj", 5632u, 2048u, 352u, 128u}
};

inline std::uint64_t tile_count(const LinearOpPlan& op) {
  return static_cast<std::uint64_t>(op.input_tiles) *
         static_cast<std::uint64_t>(op.output_tiles);
}

inline std::uint64_t commands_per_layer() {
  std::uint64_t total = 0;
  for (std::uint32_t op = 0; op < kLinearOpCount; ++op) {
    total += tile_count(kLinearOps[op]);
  }
  return total;
}

inline std::uint64_t total_tile_commands() {
  return static_cast<std::uint64_t>(kLayerCount) * commands_per_layer();
}

inline TileCommand command_for(
    std::uint32_t layer,
    std::uint32_t op_index,
    std::uint32_t input_tile,
    std::uint32_t output_tile) {
  const LinearOpPlan& op = kLinearOps[op_index];
  const std::uint32_t input_addr =
      kInputBase + layer * kLayerInputStride + op_index * kOpInputStride +
      input_tile * kTileBytes;
  const std::uint32_t weight_addr =
      kWeightBase + layer * kLayerWeightStride + op_index * kOpWeightStride +
      (output_tile * op.input_tiles + input_tile) * kTileBytes;
  const std::uint32_t output_addr =
      kOutputBase + layer * kLayerOutputStride + op_index * kOpOutputStride +
      output_tile * kTileBytes;
  return {
      input_addr,
      weight_addr,
      output_addr,
      kTileRows,
      kTileCols,
      kTileDepth,
  };
}

inline std::uint64_t mix_digest_u32(std::uint64_t digest, std::uint32_t value) {
  for (std::uint32_t shift = 0; shift < 32; shift += 8) {
    digest ^= static_cast<std::uint8_t>((value >> shift) & 0xffu);
    digest *= kCommandDigestPrime;
  }
  return digest;
}

inline std::uint64_t mix_tile_command_digest(
    std::uint64_t digest,
    const TileCommand& command) {
  digest = mix_digest_u32(digest, command.input_addr);
  digest = mix_digest_u32(digest, command.weight_addr);
  digest = mix_digest_u32(digest, command.output_addr);
  digest = mix_digest_u32(digest, command.rows);
  digest = mix_digest_u32(digest, command.cols);
  digest = mix_digest_u32(digest, command.depth);
  return digest;
}

inline std::uint64_t command_stream_digest() {
  std::uint64_t digest = kCommandDigestOffsetBasis;
  for (std::uint32_t layer = 0; layer < kLayerCount; ++layer) {
    for (std::uint32_t op_index = 0; op_index < kLinearOpCount; ++op_index) {
      const LinearOpPlan& op = kLinearOps[op_index];
      for (std::uint32_t output_tile = 0; output_tile < op.output_tiles; ++output_tile) {
        for (std::uint32_t input_tile = 0; input_tile < op.input_tiles; ++input_tile) {
          digest = mix_tile_command_digest(
              digest,
              command_for(layer, op_index, input_tile, output_tile));
        }
      }
    }
  }
  return digest;
}

}  // namespace e1_device::tinyllama_full

#endif  // E1_CODE_PROGRAM_E1_TINYLLAMA_FULL_SCHEDULE_HPP
