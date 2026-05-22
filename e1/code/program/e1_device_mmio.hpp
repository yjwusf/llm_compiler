#ifndef E1_CODE_PROGRAM_E1_DEVICE_MMIO_HPP
#define E1_CODE_PROGRAM_E1_DEVICE_MMIO_HPP

#include <cstdint>

namespace e1_device {

constexpr std::uintptr_t kArrayBase = 0x40000000u;

constexpr std::uintptr_t kArrayInputAddr = kArrayBase + 0x00u;
constexpr std::uintptr_t kArrayWeightAddr = kArrayBase + 0x04u;
constexpr std::uintptr_t kArrayOutputAddr = kArrayBase + 0x08u;
constexpr std::uintptr_t kArrayRows = kArrayBase + 0x0cu;
constexpr std::uintptr_t kArrayCols = kArrayBase + 0x10u;
constexpr std::uintptr_t kArrayDepth = kArrayBase + 0x14u;
constexpr std::uintptr_t kArrayStart = kArrayBase + 0x18u;
constexpr std::uintptr_t kArrayStatus = kArrayBase + 0x1cu;

constexpr std::uint32_t kArrayStatusBusy = 1u << 0;
constexpr std::uint32_t kArrayStartRun = 1u;

#ifdef E1_DEVICE_HOST_MODEL
void write32(std::uintptr_t addr, std::uint32_t value);
std::uint32_t read32(std::uintptr_t addr);
#else
inline void write32(std::uintptr_t addr, std::uint32_t value) {
  *reinterpret_cast<volatile std::uint32_t*>(addr) = value;
}

inline std::uint32_t read32(std::uintptr_t addr) {
  return *reinterpret_cast<volatile const std::uint32_t*>(addr);
}
#endif

inline void wait_for_array_idle() {
  while ((read32(kArrayStatus) & kArrayStatusBusy) != 0u) {
  }
}

}  // namespace e1_device

#endif  // E1_CODE_PROGRAM_E1_DEVICE_MMIO_HPP
