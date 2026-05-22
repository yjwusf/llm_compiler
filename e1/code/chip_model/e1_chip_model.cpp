#include "e1_chip_model.hpp"

#include <algorithm>

namespace e1 {

namespace {

SystolicCommand first_mock_command() {
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

void ControlCpuModel::reset() {
  state_ = State::kReset;
}

void ControlCpuModel::tick(
    bool cmd_ready,
    bool array_done,
    bool array_error,
    PerfCounters& counters) {
  ++counters.cycles;

  switch (state_) {
    case State::kReset:
      state_ = State::kIssue;
      break;
    case State::kIssue:
      if (cmd_ready) {
        ++counters.instructions;
        ++counters.array_commands;
        state_ = State::kWait;
      } else {
        ++counters.stall_cycles;
      }
      break;
    case State::kWait:
      if (array_error) {
        ++counters.error_events;
        state_ = State::kHalted;
      } else if (array_done) {
        state_ = State::kHalted;
      }
      break;
    case State::kHalted:
      break;
  }
}

bool ControlCpuModel::command_valid() const {
  return state_ == State::kIssue;
}

SystolicCommand ControlCpuModel::command() const {
  return first_mock_command();
}

bool ControlCpuModel::halted() const {
  return state_ == State::kHalted;
}

void RgmiiEthernetIngressModel::reset() {
  payload_.clear();
}

void RgmiiEthernetIngressModel::load_payload(
    const std::vector<std::uint8_t>& bytes,
    PerfCounters& counters) {
  for (std::uint8_t byte : bytes) {
    payload_.push_back(byte);
  }
  if (!bytes.empty()) {
    ++counters.frames_seen;
  }
  counters.rgmii_rx_cycles += bytes.size();
  counters.input_transfers += bytes.size();
}

void RgmiiEthernetIngressModel::tick(
    StreamSramModel& ingress_sram,
    PerfCounters& counters) {
  if (payload_.empty()) {
    return;
  }
  if (ingress_sram.accept_byte(payload_.front())) {
    payload_.pop_front();
  } else {
    ++counters.stall_cycles;
  }
}

bool RgmiiEthernetIngressModel::idle() const {
  return payload_.empty();
}

void StreamSramModel::reset() {
  staged_bytes_.clear();
}

bool StreamSramModel::accept_byte(std::uint8_t byte) {
  staged_bytes_.push_back(byte);
  return true;
}

void StreamSramModel::tick(PerfCounters& counters) {
  if (staged_bytes_.empty()) {
    return;
  }
  staged_bytes_.pop_front();
  ++counters.input_transfers;
}

bool StreamSramModel::idle() const {
  return staged_bytes_.empty();
}

ConfigSramModel::ConfigSramModel(
    std::uint32_t size_bytes,
    std::uint32_t data_width,
    std::uint32_t banks)
    : size_bytes_(size_bytes), data_width_(data_width), banks_(banks) {}

void ConfigSramModel::reset() {
  initialized_ = false;
}

void ConfigSramModel::tick(PerfCounters& counters) {
  if (!initialized_) {
    initialized_ = true;
    ++counters.cycles;
  }
}

bool ConfigSramModel::initialized() const {
  return initialized_;
}

std::uint32_t ConfigSramModel::size_bytes() const {
  return size_bytes_;
}

std::uint32_t ConfigSramModel::data_width() const {
  return data_width_;
}

std::uint32_t ConfigSramModel::banks() const {
  return banks_;
}

void SystolicArrayModel::submit(SystolicCommand command) {
  pending_.push_back(command);
}

void SystolicArrayModel::tick(PerfCounters& counters) {
  if (cycles_remaining_ > 0) {
    --cycles_remaining_;
    if (cycles_remaining_ == 0) {
      ++counters.output_transfers;
    }
    return;
  }

  if (pending_.empty()) {
    return;
  }

  const SystolicCommand command = pending_.front();
  pending_.pop_front();

  ++counters.array_commands;
  ++counters.input_transfers;

  const std::uint32_t work =
      static_cast<std::uint32_t>(command.rows) *
      static_cast<std::uint32_t>(command.cols) *
      std::max<std::uint32_t>(1, command.depth);
  cycles_remaining_ = std::max<std::uint32_t>(1, work / 16);
}

bool SystolicArrayModel::busy() const {
  return cycles_remaining_ != 0 || !pending_.empty();
}

ChipModel::ChipModel()
    : activation_sram_(524288, 128, 8),
      accumulator_sram_(524288, 256, 8) {}

void ChipModel::reset() {
  counters_ = {};
  control_cpu_.reset();
  rgmii_ingress_.reset();
  ingress_sram_.reset();
  activation_sram_.reset();
  accumulator_sram_.reset();
  systolic_array_ = {};
}

void ChipModel::load_ethernet_payload(const std::vector<std::uint8_t>& bytes) {
  rgmii_ingress_.load_payload(bytes, counters_);
}

void ChipModel::write_array_command(SystolicCommand command) {
  systolic_array_.submit(command);
  ++counters_.instructions;
}

void ChipModel::tick() {
  ++counters_.cycles;
  rgmii_ingress_.tick(ingress_sram_, counters_);
  ingress_sram_.tick(counters_);
  activation_sram_.tick(counters_);
  accumulator_sram_.tick(counters_);
  systolic_array_.tick(counters_);
}

void ChipModel::run_until_idle(std::uint64_t max_cycles) {
  for (std::uint64_t i = 0; i < max_cycles && !idle(); ++i) {
    tick();
  }
  if (!idle()) {
    ++counters_.error_events;
  }
}

bool ChipModel::idle() const {
  return rgmii_ingress_.idle() && ingress_sram_.idle() && !systolic_array_.busy();
}

const PerfCounters& ChipModel::counters() const {
  return counters_;
}

}  // namespace e1
