#ifndef E1_CODE_CHIP_MODEL_E1_CHIP_MODEL_HPP
#define E1_CODE_CHIP_MODEL_E1_CHIP_MODEL_HPP

#include <cstdint>
#include <deque>
#include <vector>

namespace e1 {

struct PerfCounters {
  std::uint64_t cycles = 0;
  std::uint64_t instructions = 0;
  std::uint64_t input_transfers = 0;
  std::uint64_t output_transfers = 0;
  std::uint64_t stall_cycles = 0;
  std::uint64_t error_events = 0;
  std::uint64_t array_commands = 0;
};

struct SystolicCommand {
  std::uint32_t input_addr = 0;
  std::uint32_t weight_addr = 0;
  std::uint32_t output_addr = 0;
  std::uint16_t rows = 0;
  std::uint16_t cols = 0;
  std::uint16_t depth = 0;
};

class SystolicArrayModel {
 public:
  void submit(SystolicCommand command);
  void tick(PerfCounters& counters);
  bool busy() const;

 private:
  std::deque<SystolicCommand> pending_;
  std::uint32_t cycles_remaining_ = 0;
};

class ChipModel {
 public:
  void reset();
  void load_ethernet_payload(const std::vector<std::uint8_t>& bytes);
  void write_array_command(SystolicCommand command);
  void tick();
  void run_until_idle(std::uint64_t max_cycles);

  bool idle() const;
  const PerfCounters& counters() const;

 private:
  PerfCounters counters_;
  SystolicArrayModel systolic_array_;
  std::deque<std::uint8_t> ingress_bytes_;
};

}  // namespace e1

#endif  // E1_CODE_CHIP_MODEL_E1_CHIP_MODEL_HPP
