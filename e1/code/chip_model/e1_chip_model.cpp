#include "e1_chip_model.hpp"

#include <algorithm>

namespace e1 {

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

void ChipModel::reset() {
  counters_ = {};
  systolic_array_ = {};
  ingress_bytes_.clear();
}

void ChipModel::load_ethernet_payload(const std::vector<std::uint8_t>& bytes) {
  for (std::uint8_t byte : bytes) {
    ingress_bytes_.push_back(byte);
  }
  counters_.input_transfers += bytes.size();
}

void ChipModel::write_array_command(SystolicCommand command) {
  systolic_array_.submit(command);
  ++counters_.instructions;
}

void ChipModel::tick() {
  ++counters_.cycles;
  if (!ingress_bytes_.empty()) {
    ingress_bytes_.pop_front();
  }
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
  return ingress_bytes_.empty() && !systolic_array_.busy();
}

const PerfCounters& ChipModel::counters() const {
  return counters_;
}

}  // namespace e1
