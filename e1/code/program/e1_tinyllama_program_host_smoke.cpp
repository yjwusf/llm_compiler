#include "e1_device_mmio.hpp"

#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>

extern "C" void e1_main();

namespace {

struct Write {
  std::uintptr_t addr;
  std::uint32_t value;
};

std::vector<Write> writes;
std::uint32_t status_reads = 0;

bool write_matches(
    std::size_t index,
    std::uintptr_t addr,
    std::uint32_t value) {
  return index < writes.size() && writes[index].addr == addr && writes[index].value == value;
}

}  // namespace

namespace e1_device {

void write32(std::uintptr_t addr, std::uint32_t value) {
  writes.push_back({addr, value});
}

std::uint32_t read32(std::uintptr_t addr) {
  assert(addr == kArrayStatus);
  ++status_reads;
  return 0;
}

}  // namespace e1_device

int main() {
  e1_main();

  using namespace e1_device;
  const bool pass =
      writes.size() == 7 &&
      write_matches(0, kArrayInputAddr, 0x00010000u) &&
      write_matches(1, kArrayWeightAddr, 0x00040000u) &&
      write_matches(2, kArrayOutputAddr, 0x00080000u) &&
      write_matches(3, kArrayRows, 16) &&
      write_matches(4, kArrayCols, 16) &&
      write_matches(5, kArrayDepth, 16) &&
      write_matches(6, kArrayStart, kArrayStartRun) &&
      status_reads == 2;

  std::cout
      << "{\n"
      << "  \"schema\": \"e1-device-program-smoke-v0\",\n"
      << "  \"status\": \"" << (pass ? "pass" : "fail") << "\",\n"
      << "  \"program\": \"first_attention_tile\",\n"
      << "  \"writes\": " << writes.size() << ",\n"
      << "  \"status_reads\": " << status_reads << "\n"
      << "}\n";

  return pass ? 0 : 1;
}
