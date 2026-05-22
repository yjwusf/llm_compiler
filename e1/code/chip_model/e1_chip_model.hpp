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
  std::uint64_t rgmii_rx_cycles = 0;
  std::uint64_t frames_seen = 0;
};

struct SystolicCommand {
  std::uint32_t input_addr = 0;
  std::uint32_t weight_addr = 0;
  std::uint32_t output_addr = 0;
  std::uint16_t rows = 0;
  std::uint16_t cols = 0;
  std::uint16_t depth = 0;
};

class StreamSramModel;

class ControlCpuModel {
 public:
  void reset();
  void tick(bool cmd_ready, bool array_done, bool array_error, PerfCounters& counters);

  bool command_valid() const;
  SystolicCommand command() const;
  bool halted() const;

 private:
  enum class State {
    kReset,
    kIssue,
    kWait,
    kHalted,
  };

  State state_ = State::kReset;
};

class RgmiiEthernetIngressModel {
 public:
  void reset();
  void load_payload(const std::vector<std::uint8_t>& bytes, PerfCounters& counters);
  void tick(StreamSramModel& ingress_sram, PerfCounters& counters);
  bool idle() const;

 private:
  std::deque<std::uint8_t> payload_;
};

class StreamSramModel {
 public:
  void reset();
  bool accept_byte(std::uint8_t byte);
  void tick(PerfCounters& counters);
  bool idle() const;

 private:
  std::deque<std::uint8_t> staged_bytes_;
};

class ConfigSramModel {
 public:
  ConfigSramModel(std::uint32_t size_bytes, std::uint32_t data_width, std::uint32_t banks);

  void reset();
  void tick(PerfCounters& counters);
  bool initialized() const;
  std::uint32_t size_bytes() const;
  std::uint32_t data_width() const;
  std::uint32_t banks() const;

 private:
  std::uint32_t size_bytes_ = 0;
  std::uint32_t data_width_ = 0;
  std::uint32_t banks_ = 0;
  bool initialized_ = false;
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
  ChipModel();

  void reset();
  void load_ethernet_payload(const std::vector<std::uint8_t>& bytes);
  void write_array_command(SystolicCommand command);
  void tick();
  void run_until_idle(std::uint64_t max_cycles);

  bool idle() const;
  const PerfCounters& counters() const;

 private:
  PerfCounters counters_;
  ControlCpuModel control_cpu_;
  RgmiiEthernetIngressModel rgmii_ingress_;
  StreamSramModel ingress_sram_;
  ConfigSramModel activation_sram_;
  ConfigSramModel accumulator_sram_;
  SystolicArrayModel systolic_array_;
};

}  // namespace e1

#endif  // E1_CODE_CHIP_MODEL_E1_CHIP_MODEL_HPP
