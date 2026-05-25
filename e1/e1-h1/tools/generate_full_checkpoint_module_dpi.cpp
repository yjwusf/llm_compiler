// SPDX-License-Identifier: Apache-2.0
// Generate per-module DPI probes for generated full-checkpoint RTL modules.

#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

struct SignalSpec {
  std::string name;
  std::string width;
  std::string description;
};

struct CycleStep {
  int cycle;
  std::string phase;
  std::string responsibility;
  std::string observed_signals;
  std::string dpi_check;
};

struct ModuleSpec {
  std::string name;
  std::string top_module;
  std::string probe_module;
  std::vector<std::string> rtl;
  std::vector<std::string> cycle_notes;
  std::vector<SignalSpec> input_signals;
  std::vector<SignalSpec> output_signals;
  std::string probe_sv;
};

struct PortDecl {
  std::string name;
  std::string direction;
  std::string width;
};

const std::vector<std::string>& known_child_modules() {
  static const std::vector<std::string> modules = {
      "e1_h1_tinyllama_linear_scheduler",
      "e1_h1_tinyllama_linear_tile_engine",
      "e1_h1_tinyllama_control_scheduler",
      "e1_h1_tinyllama_graph_sequencer",
      "e1_h1_tinyllama_linear_slot_engine",
      "e1_h1_tinyllama_control_slot_engine",
      "e1_h1_tinyllama_full_checkpoint_top",
      "e1_h1_stream_sram",
      "e1_h1_systolic_array",
  };
  return modules;
}

std::vector<std::string> allowed_child_modules(const ModuleSpec& spec) {
  if (spec.name == "linear_tile_engine") {
    return {
        "e1_h1_tinyllama_linear_scheduler",
        "e1_h1_stream_sram",
        "e1_h1_systolic_array",
    };
  }
  if (spec.name == "linear_slot_engine") {
    return {
        "e1_h1_stream_sram",
        "e1_h1_systolic_array",
    };
  }
  if (spec.name == "full_checkpoint_top") {
    return {
        "e1_h1_tinyllama_graph_sequencer",
        "e1_h1_tinyllama_linear_slot_engine",
        "e1_h1_tinyllama_control_slot_engine",
    };
  }
  return {};
}

std::vector<std::string> child_stub_modules(const ModuleSpec& spec) {
  return allowed_child_modules(spec);
}

std::vector<std::string> module_only_flist_rtl(const ModuleSpec& spec) {
  return {spec.rtl.back()};
}

std::vector<std::string> composed_rtl_dependencies(const ModuleSpec& spec) {
  std::vector<std::string> dependencies;
  for (std::size_t i = 0; i + 1 < spec.rtl.size(); ++i) {
    dependencies.push_back(spec.rtl[i]);
  }
  return dependencies;
}

std::vector<std::string> forbidden_child_modules(const ModuleSpec& spec) {
  const std::vector<std::string> allowed = allowed_child_modules(spec);
  std::vector<std::string> forbidden;
  for (const std::string& module : known_child_modules()) {
    if (module == spec.top_module) {
      continue;
    }
    bool is_allowed = false;
    for (const std::string& child : allowed) {
      if (child == module) {
        is_allowed = true;
      }
    }
    if (!is_allowed) {
      forbidden.push_back(module);
    }
  }
  return forbidden;
}

std::string read_text(const fs::path& path) {
  std::ifstream input(path);
  if (!input) {
    throw std::runtime_error("cannot read " + path.string());
  }
  std::ostringstream buffer;
  buffer << input.rdbuf();
  return buffer.str();
}

void write_text(const fs::path& path, const std::string& text) {
  fs::create_directories(path.parent_path());
  std::ofstream output(path);
  if (!output) {
    throw std::runtime_error("cannot write " + path.string());
  }
  output << text;
}

bool contains(const std::string& text, const std::string& needle) {
  return text.find(needle) != std::string::npos;
}

std::string trim(const std::string& text) {
  std::size_t first = 0;
  while (first < text.size() && std::isspace(static_cast<unsigned char>(text[first]))) {
    ++first;
  }
  std::size_t last = text.size();
  while (last > first && std::isspace(static_cast<unsigned char>(text[last - 1]))) {
    --last;
  }
  return text.substr(first, last - first);
}

std::vector<std::string> split_ws(const std::string& text) {
  std::istringstream input(text);
  std::vector<std::string> tokens;
  std::string token;
  while (input >> token) {
    tokens.push_back(token);
  }
  return tokens;
}

std::string width_from_range(const std::string& token) {
  if (token.size() < 5 || token.front() != '[' || token.back() != ']') {
    throw std::runtime_error("unsupported RTL port width token " + token);
  }
  const std::string body = token.substr(1, token.size() - 2);
  const std::size_t colon = body.find(':');
  if (colon == std::string::npos || body.substr(colon + 1) != "0") {
    throw std::runtime_error("unsupported RTL port range " + token);
  }
  return std::to_string(std::stoi(body.substr(0, colon)) + 1);
}

std::string module_port_block(const std::string& rtl, const std::string& top_module) {
  const std::string module_marker = "module " + top_module;
  const std::size_t module_pos = rtl.find(module_marker);
  if (module_pos == std::string::npos) {
    throw std::runtime_error("RTL does not define " + top_module);
  }
  const std::size_t port_start = rtl.find('(', module_pos);
  const std::size_t port_end = rtl.find("\n);", port_start);
  if (port_start == std::string::npos || port_end == std::string::npos) {
    throw std::runtime_error("cannot parse port block for " + top_module);
  }
  return rtl.substr(port_start + 1, port_end - port_start - 1);
}

std::vector<PortDecl> parse_ports(const std::string& port_block) {
  std::vector<PortDecl> ports;
  std::istringstream lines(port_block);
  std::string line;
  while (std::getline(lines, line)) {
    line = trim(line);
    if (line.empty()) {
      continue;
    }
    if (!line.empty() && line.back() == ',') {
      line.pop_back();
    }
    const std::vector<std::string> tokens = split_ws(line);
    if (tokens.empty() || (tokens[0] != "input" && tokens[0] != "output")) {
      continue;
    }
    if (tokens.size() != 3 && tokens.size() != 4) {
      throw std::runtime_error("unsupported RTL port declaration: " + line);
    }
    if (tokens[1] != "logic") {
      throw std::runtime_error("RTL port declaration must use logic: " + line);
    }
    const std::string width = tokens.size() == 3 ? "1" : width_from_range(tokens[2]);
    ports.push_back({tokens.back(), tokens[0], width});
  }
  return ports;
}

std::vector<PortDecl> ports_for_direction(const std::vector<PortDecl>& ports,
                                          const std::string& direction) {
  std::vector<PortDecl> selected;
  for (const PortDecl& port : ports) {
    if (port.direction == direction) {
      selected.push_back(port);
    }
  }
  return selected;
}

std::vector<PortDecl> expected_input_ports(const ModuleSpec& spec) {
  std::vector<PortDecl> ports;
  for (const SignalSpec& signal : spec.input_signals) {
    ports.push_back({signal.name, "input", signal.width});
  }
  return ports;
}

std::vector<PortDecl> expected_output_ports(const ModuleSpec& spec) {
  std::vector<PortDecl> ports;
  for (const SignalSpec& signal : spec.output_signals) {
    ports.push_back({signal.name, "output", signal.width});
  }
  return ports;
}

void require_matching_ports(const std::string& name,
                            const std::string& direction,
                            const std::vector<PortDecl>& actual,
                            const std::vector<PortDecl>& expected) {
  if (actual.size() != expected.size()) {
    throw std::runtime_error(name + " generated " + direction +
                             " signal docs do not cover every RTL port");
  }
  for (std::size_t i = 0; i < expected.size(); ++i) {
    if (actual[i].name != expected[i].name ||
        actual[i].direction != expected[i].direction ||
        actual[i].width != expected[i].width) {
      throw std::runtime_error(
          name + " RTL " + direction + " port contract mismatch at index " +
          std::to_string(i));
    }
  }
}

void validate_signal_contract(const fs::path& repo_root, const ModuleSpec& spec) {
  if (spec.input_signals.empty() || spec.output_signals.empty()) {
    throw std::runtime_error(spec.name + " has incomplete generated signal docs");
  }
  for (const SignalSpec& signal : spec.input_signals) {
    if (signal.name.empty() || signal.width.empty() || signal.description.empty()) {
      throw std::runtime_error(spec.name + " has incomplete input signal docs");
    }
  }
  for (const SignalSpec& signal : spec.output_signals) {
    if (signal.name.empty() || signal.width.empty() || signal.description.empty()) {
      throw std::runtime_error(spec.name + " has incomplete output signal docs");
    }
  }
  const fs::path dut_path = repo_root / spec.rtl.back();
  const std::string rtl = read_text(dut_path);
  const std::vector<PortDecl> actual = parse_ports(module_port_block(rtl, spec.top_module));
  require_matching_ports(
      spec.name, "input", ports_for_direction(actual, "input"), expected_input_ports(spec));
  require_matching_ports(
      spec.name, "output", ports_for_direction(actual, "output"), expected_output_ports(spec));
}

void require_contains(const std::string& text, const std::string& needle, const fs::path& path) {
  if (!contains(text, needle)) {
    throw std::runtime_error(path.string() + " is missing required text: " + needle);
  }
}

void validate_rtl_inputs(const fs::path& repo_root, const ModuleSpec& spec) {
  for (const std::string& rtl : spec.rtl) {
    const fs::path rtl_path = repo_root / rtl;
    const std::string text = read_text(rtl_path);
    if (text.find("module " + spec.top_module) == std::string::npos &&
        rtl == spec.rtl.back()) {
      throw std::runtime_error(rtl + " does not define " + spec.top_module);
    }
  }
}

int count_substring(const std::string& text, const std::string& needle) {
  int count = 0;
  std::size_t pos = 0;
  while ((pos = text.find(needle, pos)) != std::string::npos) {
    ++count;
    pos += needle.size();
  }
  return count;
}

int probe_dut_instantiation_count(const ModuleSpec& spec) {
  return count_substring(spec.probe_sv, "\n  " + spec.top_module + " u_dut (") +
         count_substring(spec.probe_sv, "\n  " + spec.top_module + " #(");
}

void validate_isolation(const fs::path& repo_root, const ModuleSpec& spec) {
  const std::string dut_text = read_text(repo_root / spec.rtl.back());
  for (const std::string& child : allowed_child_modules(spec)) {
    if (dut_text.find(child) == std::string::npos) {
      throw std::runtime_error(spec.name + " is missing allowed child " + child);
    }
  }
  for (const std::string& child : forbidden_child_modules(spec)) {
    if (dut_text.find(child) != std::string::npos) {
      throw std::runtime_error(spec.name + " unexpectedly references forbidden child " + child);
    }
  }
  if (probe_dut_instantiation_count(spec) != 1) {
    throw std::runtime_error(spec.name + " probe must instantiate exactly one DUT");
  }
  for (const std::string& child : known_child_modules()) {
    if (child == spec.top_module) {
      continue;
    }
    if (contains(spec.probe_sv, "\n  " + child + " u_") ||
        contains(spec.probe_sv, "\n  " + child + " #(")) {
      throw std::runtime_error(spec.name + " probe instantiates sibling/child module " + child);
    }
  }
}

std::string scoreboard_cpp() {
  return R"cpp(// SPDX-License-Identifier: Apache-2.0
// Generated by e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp.

#include <cstdio>

extern "C" void e1_h1_full_dpi_begin(const char* module_name, const char* vip_case) {
  std::printf("E1_H1_FULL_MODULE_DPI_BEGIN module=%s case=%s\n", module_name, vip_case);
}

extern "C" void e1_h1_full_dpi_cycle(const char* module_name, int cycle, const char* phase) {
  std::printf("E1_H1_FULL_MODULE_DPI_CYCLE module=%s cycle=%d phase=%s\n", module_name, cycle, phase);
}

extern "C" int e1_h1_full_dpi_phase_signal(
    const char* module_name,
    const char* signal_name,
    int cycle,
    int expected,
    int actual) {
  std::printf(
      "E1_H1_FULL_MODULE_DPI_PHASE_SIGNAL module=%s signal=%s cycle=%d expected=%d actual=%d\n",
      module_name,
      signal_name,
      cycle,
      expected,
      actual);
  if (expected != actual) {
    std::fprintf(
        stderr,
        "E1_H1_FULL_MODULE_DPI_PHASE_SIGNAL_MISMATCH module=%s signal=%s cycle=%d expected=%d actual=%d\n",
        module_name,
        signal_name,
        cycle,
        expected,
        actual);
    return 0;
  }
  return 1;
}

extern "C" int e1_h1_full_dpi_expect_u32(
    const char* module_name,
    const char* signal_name,
    int cycle,
    int expected,
    int actual) {
  if (expected != actual) {
    std::fprintf(
        stderr,
        "E1_H1_FULL_MODULE_DPI_MISMATCH module=%s signal=%s cycle=%d expected=0x%x actual=0x%x\n",
        module_name,
        signal_name,
        cycle,
        static_cast<unsigned>(expected),
        static_cast<unsigned>(actual));
    return 0;
  }
  return 1;
}
)cpp";
}

std::string main_cpp(const ModuleSpec& spec) {
  std::ostringstream out;
  out << "// SPDX-License-Identifier: Apache-2.0\n";
  out << "// Generated by e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp.\n\n";
  out << "#include \"V" << spec.probe_module << ".h\"\n";
  out << "#include \"verilated.h\"\n\n";
  out << "int main(int argc, char** argv) {\n";
  out << "  VerilatedContext context;\n";
  out << "  context.commandArgs(argc, argv);\n";
  out << "  V" << spec.probe_module << " top{&context};\n";
  out << "  while (!context.gotFinish() && context.time() < 200000) {\n";
  out << "    top.eval();\n";
  out << "    context.timeInc(1);\n";
  out << "  }\n";
  out << "  return context.gotFinish() ? 0 : 1;\n";
  out << "}\n";
  return out.str();
}

std::string flist_text(const fs::path& probe, const ModuleSpec& spec) {
  std::ostringstream out;
  for (const std::string& rtl : module_only_flist_rtl(spec)) {
    out << rtl << "\n";
  }
  out << probe.generic_string() << "\n";
  return out.str();
}

void write_signal_array_json(std::ostringstream& out,
                             const std::string& key,
                             const std::vector<SignalSpec>& signals,
                             const std::string& indent) {
  out << indent << "\"" << key << "\": [";
  if (!signals.empty()) {
    out << "\n";
  }
  for (std::size_t i = 0; i < signals.size(); ++i) {
    const SignalSpec& signal = signals[i];
    out << indent << "  {\"name\": \"" << signal.name << "\", \"width\": \"" << signal.width
        << "\", \"description\": \"" << signal.description << "\"}"
        << (i + 1 == signals.size() ? "\n" : ",\n");
  }
  out << indent << "]";
}

void write_string_array_json(std::ostringstream& out,
                             const std::string& key,
                             const std::vector<std::string>& values,
                             const std::string& indent) {
  out << indent << "\"" << key << "\": [";
  for (std::size_t i = 0; i < values.size(); ++i) {
    out << (i == 0 ? "" : ", ") << "\"" << values[i] << "\"";
  }
  out << "]";
}

std::vector<std::string> cycle_phase_signals(const ModuleSpec& spec) {
  std::vector<std::string> signals;
  for (const SignalSpec& signal : spec.output_signals) {
    if (signal.name.find("cycle_phase") != std::string::npos) {
      signals.push_back(signal.name);
    }
  }
  return signals;
}

std::string primary_phase_signal(const ModuleSpec& spec) {
  return spec.name == "full_checkpoint_top" ? "graph_cycle_phase_o" : "cycle_phase_o";
}

void require_primary_phase_signal(const ModuleSpec& spec) {
  const std::vector<std::string> signals = cycle_phase_signals(spec);
  const std::string primary = primary_phase_signal(spec);
  for (const std::string& signal : signals) {
    if (signal == primary) {
      return;
    }
  }
  throw std::runtime_error(spec.name + " does not document primary phase signal " + primary);
}

std::vector<CycleStep> cycle_steps(const ModuleSpec& spec);

void write_phase_signal_trace_json(std::ostringstream& out,
                                   const std::string& key,
                                   const ModuleSpec& spec,
                                   const std::string& indent) {
  const std::vector<CycleStep> steps = cycle_steps(spec);
  const std::string signal = primary_phase_signal(spec);
  out << indent << "\"" << key << "\": [\n";
  for (std::size_t i = 0; i < steps.size(); ++i) {
    out << indent << "  {\"cycle\": " << steps[i].cycle
        << ", \"signal\": \"" << signal
        << "\", \"expected\": " << steps[i].cycle << "}"
        << (i + 1 == steps.size() ? "\n" : ",\n");
  }
  out << indent << "]";
}

std::string cycle_template_name(const ModuleSpec& spec) {
  if (spec.name == "linear_scheduler" || spec.name == "linear_tile_engine" ||
      spec.name == "linear_slot_engine") {
    return "tile_command_8_cycle_cpu_latch_array_template";
  }
  if (spec.name == "control_scheduler" || spec.name == "control_slot_engine") {
    return "control_op_4_cycle_cpu_template";
  }
  if (spec.name == "graph_sequencer") {
    return "graph_slot_4_cycle_launch_template";
  }
  if (spec.name == "full_checkpoint_top") {
    return "top_dispatch_4_cycle_slot_engine_template";
  }
  throw std::runtime_error("no cycle template for " + spec.name);
}

std::vector<CycleStep> cycle_steps(const ModuleSpec& spec) {
  if (spec.name == "linear_scheduler") {
    return {
        {0, "setup_tile_command", "Select the next TinyLlama linear tile command.",
         "cycle_phase_o, layer_o, op_index_o, input_tile_o, output_tile_o",
         "DPI records the phase and later checks issued_commands_o."},
        {1, "assert_scheduler_valid", "Present cmd_valid_o and hold the payload stable.",
         "cycle_phase_o, cmd_valid_o, cmd_*_o", "DPI records the valid phase."},
        {2, "accept_command_handshake", "Accept the command when cmd_ready_i is high.",
         "cycle_phase_o, cmd_valid_o, cmd_ready_i", "DPI records the handshake phase."},
        {3, "wait_for_array_progress_0", "Wait for the first array progress beat.",
         "cycle_phase_o, array_done_i", "DPI records the in-flight phase."},
        {4, "wait_for_array_progress_1", "Wait for the second array progress beat.",
         "cycle_phase_o, array_done_i", "DPI records the in-flight phase."},
        {5, "wait_for_array_progress_2", "Wait for the third array progress beat.",
         "cycle_phase_o, array_done_i", "DPI records the in-flight phase."},
        {6, "sample_array_done", "Sample array_done_i or array_error_i for the command.",
         "cycle_phase_o, array_done_i, array_error_i", "DPI drives array_done_i in this phase."},
        {7, "advance_tile_counters", "Advance layer/op/tile counters for the next command.",
         "cycle_phase_o, issued_commands_o", "DPI checks the accepted command count."},
    };
  }
  if (spec.name == "linear_tile_engine") {
    return {
        {0, "setup_tile_engine", "Start the scheduler/latch/array composition.",
         "cycle_phase_o, scheduler_cmd_valid_o", "DPI records the composed engine phase."},
        {1, "scheduler_valid_visible", "Expose the ungated scheduler command-valid.",
         "cycle_phase_o, scheduler_cmd_valid_o", "DPI records valid before array valid."},
        {2, "array_command_handshake", "Gate the command into the systolic array.",
         "cycle_phase_o, array_cmd_valid_o, array_cmd_ready_o", "DPI observes the array handshake."},
        {3, "latch_to_array_beat_0", "Present the first latched stream beat to the array.",
         "cycle_phase_o, buffer_array_valid_o, buffer_array_data_o", "DPI records latch output."},
        {4, "latch_to_array_beat_1", "Stage or forward the next stream beat.",
         "cycle_phase_o, buffer_array_valid_o, buffer_array_ready_o", "DPI records latch readiness."},
        {5, "latch_to_array_beat_2", "Continue array input transfer.",
         "cycle_phase_o, buffer_array_valid_o, buffer_array_ready_o", "DPI records latch transfer."},
        {6, "array_done_pulse", "Observe the systolic-array completion pulse.",
         "cycle_phase_o, array_done_o, array_debug_busy_o", "DPI checks completion progress."},
        {7, "return_ready", "Return the composition to ready for the next tile.",
         "cycle_phase_o, issued_commands_o", "DPI checks issued command progress."},
    };
  }
  if (spec.name == "control_scheduler") {
    return {
        {0, "issue_control_op", "Present the current non-linear control op.",
         "cycle_phase_o, control_valid_o, layer_o, layer_op_slot_o", "DPI records issue."},
        {1, "read_control_metadata", "Read source/control metadata for the op.",
         "cycle_phase_o, control_kind_o", "DPI records metadata phase."},
        {2, "execute_control_op", "Execute the CPU/control operation.",
         "cycle_phase_o, control_ready_i", "DPI records execute phase."},
        {3, "commit_control_op", "Commit the op and advance counters.",
         "cycle_phase_o, control_commit_o, issued_control_ops_o", "DPI checks committed count."},
    };
  }
  if (spec.name == "graph_sequencer") {
    return {
        {0, "present_graph_slot", "Present the ordered TinyLlama layer graph slot.",
         "cycle_phase_o, slot_valid_o, layer_o, layer_slot_o", "DPI records slot issue."},
        {1, "launch_selected_engine", "Launch either the control or linear slot engine.",
         "cycle_phase_o, launch_control_o, launch_linear_o", "DPI counts launch pulses."},
        {2, "wait_for_slot_done", "Wait for the selected slot engine to complete.",
         "cycle_phase_o, op_done_i", "DPI drives op_done_i in this phase."},
        {3, "commit_graph_slot", "Commit the graph slot and advance layer/slot counters.",
         "cycle_phase_o, issued_graph_slots_o", "DPI checks committed slot count."},
    };
  }
  if (spec.name == "linear_slot_engine") {
    return {
        {0, "latch_selected_linear_slot", "Latch graph-selected layer and linear op.",
         "cycle_phase_o, layer_o, op_index_o", "DPI records selected slot."},
        {1, "slot_command_valid", "Expose the slot-local scheduler command-valid.",
         "cycle_phase_o, scheduler_cmd_valid_o", "DPI records scheduler valid."},
        {2, "array_command_handshake", "Gate the slot command into the systolic array.",
         "cycle_phase_o, array_cmd_valid_o, array_cmd_ready_o", "DPI observes the array handshake."},
        {3, "latch_to_array_beat_0", "Present the first latched stream beat to the array.",
         "cycle_phase_o, buffer_array_valid_o, buffer_array_data_o", "DPI records latch output."},
        {4, "latch_to_array_beat_1", "Stage or forward the next stream beat.",
         "cycle_phase_o, buffer_array_valid_o, buffer_array_ready_o", "DPI records latch readiness."},
        {5, "latch_to_array_beat_2", "Continue array input transfer.",
         "cycle_phase_o, buffer_array_valid_o, buffer_array_ready_o", "DPI records latch transfer."},
        {6, "array_done_pulse", "Observe the systolic-array completion pulse.",
         "cycle_phase_o, array_done_o, array_debug_busy_o", "DPI checks completion progress."},
        {7, "slot_done_or_next_tile", "Finish the slot or advance to its next tile.",
         "cycle_phase_o, issued_commands_o, expected_commands_o", "DPI checks bounded command count."},
    };
  }
  if (spec.name == "control_slot_engine") {
    return {
        {0, "issue_selected_control_slot", "Present the selected control graph slot.",
         "cycle_phase_o, control_valid_o, layer_o", "DPI records issue."},
        {1, "read_selected_control_metadata", "Read selected control-kind metadata.",
         "cycle_phase_o, control_kind_o", "DPI records metadata phase."},
        {2, "execute_selected_control_slot", "Execute the selected CPU/control operation.",
         "cycle_phase_o, control_ready_i", "DPI records execute phase."},
        {3, "commit_selected_control_slot", "Commit the selected control slot.",
         "cycle_phase_o, control_commit_o, issued_control_ops_o", "DPI checks committed count."},
    };
  }
  if (spec.name == "full_checkpoint_top") {
    return {
        {0, "present_top_graph_slot", "Present the next graph slot at the top boundary.",
         "graph_cycle_phase_o, active_layer_o, active_slot_o", "DPI records top slot issue."},
        {1, "start_selected_slot_engine", "Pulse exactly one selected slot engine.",
         "graph_cycle_phase_o, launch_linear_o, launch_control_o", "DPI counts launch pulses."},
        {2, "run_selected_slot_engine", "Run either the control slot or linear slot engine.",
         "graph_cycle_phase_o, linear_cycle_phase_o, control_cycle_phase_o",
         "DPI records selected engine progress."},
        {3, "commit_top_graph_slot", "Return slot done to the graph sequencer.",
         "graph_cycle_phase_o, issued_graph_slots_o", "DPI checks top committed slots."},
    };
  }
  throw std::runtime_error("no cycle steps for " + spec.name);
}

void validate_cycle_contract(const ModuleSpec& spec) {
  const std::vector<CycleStep> steps = cycle_steps(spec);
  const std::vector<std::string> phase_signals = cycle_phase_signals(spec);
  if (steps.empty()) {
    throw std::runtime_error(spec.name + " has no generated cycle contract");
  }
  if (phase_signals.empty()) {
    throw std::runtime_error(spec.name + " has no documented cycle phase signal");
  }
  require_primary_phase_signal(spec);
  for (std::size_t i = 0; i < steps.size(); ++i) {
    if (steps[i].cycle != static_cast<int>(i)) {
      throw std::runtime_error(spec.name + " cycle contract is not contiguous");
    }
    if (steps[i].phase.empty() || steps[i].responsibility.empty() ||
        steps[i].observed_signals.empty() || steps[i].dpi_check.empty()) {
      throw std::runtime_error(spec.name + " has an incomplete cycle contract row");
    }
  }
  const std::string expected_dpi = "e1_h1_full_dpi_cycle(\"" + spec.name + "\"";
  if (spec.probe_sv.find(expected_dpi) == std::string::npos) {
    throw std::runtime_error(spec.name + " probe does not report DPI cycles");
  }
  const std::string phase_check =
      "expect_phase_signal(\"" + primary_phase_signal(spec) +
      "\", contract_cycle, contract_cycle % " + std::to_string(steps.size()) +
      ", int'(" + primary_phase_signal(spec) + "));";
  if (spec.probe_sv.find(phase_check) == std::string::npos) {
    throw std::runtime_error(spec.name + " probe does not check RTL phase signal");
  }
  for (const CycleStep& step : steps) {
    if (spec.probe_sv.find("\"" + step.phase + "\"") == std::string::npos) {
      throw std::runtime_error(spec.name + " probe does not report named phase " + step.phase);
    }
  }
}

void write_cycle_steps_json(std::ostringstream& out,
                            const std::vector<CycleStep>& steps,
                            const std::string& indent) {
  out << indent << "\"cycles\": [\n";
  for (std::size_t i = 0; i < steps.size(); ++i) {
    const CycleStep& step = steps[i];
    out << indent << "  {\"cycle\": " << step.cycle << ", \"phase\": \"" << step.phase
        << "\", \"responsibility\": \"" << step.responsibility
        << "\", \"observed_signals\": \"" << step.observed_signals
        << "\", \"dpi_check\": \"" << step.dpi_check << "\"}"
        << (i + 1 == steps.size() ? "\n" : ",\n");
  }
  out << indent << "]";
}

void write_cycle_contract_object_json(std::ostringstream& out,
                                      const ModuleSpec& spec,
                                      const std::string& indent) {
  const std::vector<CycleStep> steps = cycle_steps(spec);
  out << indent << "\"cycle_contract\": {\n";
  out << indent << "  \"template\": \"" << cycle_template_name(spec) << "\",\n";
  out << indent << "  \"cycle_period\": " << steps.size() << ",\n";
  out << indent << "  \"primary_phase_signal\": \"" << primary_phase_signal(spec) << "\",\n";
  write_string_array_json(out, "phase_signals", cycle_phase_signals(spec), indent + "  ");
  out << ",\n";
  write_phase_signal_trace_json(out, "expected_phase_signal_trace", spec, indent + "  ");
  out << ",\n";
  write_cycle_steps_json(out, steps, indent + "  ");
  out << "\n" << indent << "}";
}

std::string module_interfaces_markdown(const std::vector<ModuleSpec>& specs) {
  std::ostringstream out;
  out << "# Generated Full-Checkpoint RTL Module Interfaces\n\n";
  out << "Generated by `e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp`.\n";
  out << "Each section is the review contract for a generated RTL module and its "
         "module-only DPI probe.\n\n";
  for (std::size_t i = 0; i < specs.size(); ++i) {
    const ModuleSpec& spec = specs[i];
    out << "## " << spec.name << "\n\n";
    out << "- Top module: `" << spec.top_module << "`\n";
    out << "- DPI probe: `" << spec.probe_module << "`\n\n";
    out << "### Input Signals\n\n";
    out << "| Signal | Width | Description |\n";
    out << "| --- | ---: | --- |\n";
    for (const SignalSpec& signal : spec.input_signals) {
      out << "| `" << signal.name << "` | " << signal.width << " | " << signal.description << " |\n";
    }
    out << "\n### Output Signals\n\n";
    out << "| Signal | Width | Description |\n";
    out << "| --- | ---: | --- |\n";
    for (const SignalSpec& signal : spec.output_signals) {
      out << "| `" << signal.name << "` | " << signal.width << " | " << signal.description << " |\n";
    }
    out << "\n### Cycle Notes\n\n";
    for (const std::string& note : spec.cycle_notes) {
      out << "- " << note << "\n";
    }
    out << "\n### Cycle Contract\n\n";
    out << "- Template: `" << cycle_template_name(spec) << "`\n";
    out << "- Period: " << cycle_steps(spec).size() << " cycles\n";
    out << "- Phase signal(s): ";
    const std::vector<std::string> phase_signals = cycle_phase_signals(spec);
    for (std::size_t j = 0; j < phase_signals.size(); ++j) {
      out << (j == 0 ? "" : ", ") << "`" << phase_signals[j] << "`";
    }
    out << "\n\n";
    out << "| Cycle | Phase | Responsibility | Observed Signals | DPI Check |\n";
    out << "| ---: | --- | --- | --- | --- |\n";
    for (const CycleStep& step : cycle_steps(spec)) {
      out << "| " << step.cycle << " | `" << step.phase << "` | "
          << step.responsibility << " | " << step.observed_signals << " | "
          << step.dpi_check << " |\n";
    }
    if (i + 1 != specs.size()) {
      out << "\n";
    }
  }
  return out.str();
}

std::string verilator_launcher_path();

std::string manifest_json(const std::vector<ModuleSpec>& specs) {
  std::ostringstream out;
  out << "{\n";
  out << "  \"schema\": \"e1-h1-full-checkpoint-generated-module-dpi-v0\",\n";
  out << "  \"generator\": \"e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp\",\n";
  out << "  \"scoreboard\": \"e1/e1-h1/generated/full_checkpoint_dpi/e1_h1_full_checkpoint_module_dpi_scoreboard.cpp\",\n";
  out << "  \"module_interfaces_doc\": \"e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md\",\n";
  out << "  \"module_isolation_proof\": \"e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json\",\n";
  out << "  \"cycle_contract\": \"e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json\",\n";
  out << "  \"module_test_plan\": \"e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json\",\n";
  out << "  \"verilator_execution_recipe\": \"e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json\",\n";
  out << "  \"verilator_execution_launcher\": \"" << verilator_launcher_path() << "\",\n";
  out << "  \"verilator_execution_report\": \"e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json\",\n";
  out << "  \"readme_cycle_coverage\": \"e1/e1-h1/generated/full_checkpoint_dpi/readme_cycle_coverage.json\",\n";
  out << "  \"construction_ledger\": \"e1/e1-h1/generated/full_checkpoint_dpi/construction_ledger.json\",\n";
  out << "  \"construction_rule\": \"one_generated_probe_per_full_checkpoint_rtl_module_with_cpp_dpi_driven_neighbors\",\n";
  out << "  \"modules\": [\n";
  for (std::size_t i = 0; i < specs.size(); ++i) {
    const ModuleSpec& spec = specs[i];
    out << "    {\n";
    out << "      \"name\": \"" << spec.name << "\",\n";
    out << "      \"top_module\": \"" << spec.top_module << "\",\n";
  out << "      \"probe_module\": \"" << spec.probe_module << "\",\n";
  out << "      \"scope\": \"generated_full_checkpoint_module_only\",\n";
    out << "      \"neighbors\": \"cpp_dpi_environment_and_generated_child_stubs\",\n";
    out << "      \"probe\": \"e1/e1-h1/generated/full_checkpoint_dpi/" << spec.probe_module << ".sv\",\n";
    out << "      \"main\": \"e1/e1-h1/generated/full_checkpoint_dpi/" << spec.probe_module << "_main.cpp\",\n";
    out << "      \"flist\": \"e1/e1-h1/generated/full_checkpoint_dpi/flists/" << spec.name << ".f\",\n";
    out << "      \"module_test_plan\": \"e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json\",\n";
    out << "      \"verilator_execution_recipe\": \"e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json\",\n";
    out << "      \"verilator_execution_launcher\": \"" << verilator_launcher_path() << "\",\n";
    out << "      \"verilator_execution_report\": \"e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json\",\n";
    out << "      \"readme_cycle_coverage\": \"e1/e1-h1/generated/full_checkpoint_dpi/readme_cycle_coverage.json\",\n";
    out << "      \"construction_ledger\": \"e1/e1-h1/generated/full_checkpoint_dpi/construction_ledger.json\",\n";
    out << "      \"rtl\": [";
    for (std::size_t j = 0; j < spec.rtl.size(); ++j) {
      out << (j == 0 ? "" : ", ") << "\"" << spec.rtl[j] << "\"";
    }
    out << "],\n";
    write_string_array_json(out, "module_only_flist_rtl", module_only_flist_rtl(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "composed_rtl_dependencies", composed_rtl_dependencies(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "child_stub_modules", child_stub_modules(spec), "      ");
    out << ",\n";
    out << "      \"cycle_notes\": [";
    for (std::size_t j = 0; j < spec.cycle_notes.size(); ++j) {
      out << (j == 0 ? "" : ", ") << "\"" << spec.cycle_notes[j] << "\"";
    }
    out << "],\n";
    write_signal_array_json(out, "input_signals", spec.input_signals, "      ");
    out << ",\n";
    write_signal_array_json(out, "output_signals", spec.output_signals, "      ");
    out << ",\n";
    out << "      \"primary_phase_signal\": \"" << primary_phase_signal(spec) << "\",\n";
    write_phase_signal_trace_json(out, "expected_phase_signal_trace", spec, "      ");
    out << ",\n";
    write_cycle_contract_object_json(out, spec, "      ");
    out << "\n";
    out << "    }" << (i + 1 == specs.size() ? "\n" : ",\n");
  }
  out << "  ]\n";
  out << "}\n";
  return out.str();
}

std::string module_isolation_json(const std::vector<ModuleSpec>& specs) {
  std::ostringstream out;
  out << "{\n";
  out << "  \"schema\": \"e1-h1-full-checkpoint-module-isolation-v0\",\n";
  out << "  \"generator\": \"e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp\",\n";
  out << "  \"construction_rule\": \"one_probe_per_generated_module_with_only_the_selected_dut_rtl_and_generated_child_stubs\",\n";
  out << "  \"status\": \"pass\",\n";
  out << "  \"separated_boundaries\": {\n";
  out << "    \"control_modules\": [\"control_scheduler\", \"control_slot_engine\", \"graph_sequencer\"],\n";
  out << "    \"linear_modules\": [\"linear_scheduler\", \"linear_tile_engine\", \"linear_slot_engine\"],\n";
  out << "    \"latch_buffer_rtl\": \"e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv\",\n";
  out << "    \"systolic_array_rtl\": \"e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv\"\n";
  out << "  },\n";
  out << "  \"checks\": [\n";
  out << "    {\"name\": \"all_generated_probes_are_module_only\", \"status\": \"pass\"},\n";
  out << "    {\"name\": \"all_generated_probes_have_exactly_one_dut\", \"status\": \"pass\"},\n";
  out << "    {\"name\": \"all_generated_flists_are_selected_dut_plus_probe_only\", \"status\": \"pass\"},\n";
  out << "    {\"name\": \"generated_child_dependencies_are_probe_local_stubs\", \"status\": \"pass\"},\n";
  out << "    {\"name\": \"linear_path_preserves_latch_buffer_and_systolic_boundaries\", \"status\": \"pass\"},\n";
  out << "    {\"name\": \"control_path_remains_separate_from_linear_array_path\", \"status\": \"pass\"}\n";
  out << "  ],\n";
  out << "  \"modules\": [\n";
  for (std::size_t i = 0; i < specs.size(); ++i) {
    const ModuleSpec& spec = specs[i];
    out << "    {\n";
    out << "      \"name\": \"" << spec.name << "\",\n";
    out << "      \"dut_module\": \"" << spec.top_module << "\",\n";
    out << "      \"dut_rtl\": \"" << spec.rtl.back() << "\",\n";
    out << "      \"probe\": \"e1/e1-h1/generated/full_checkpoint_dpi/" << spec.probe_module << ".sv\",\n";
    out << "      \"flist\": \"e1/e1-h1/generated/full_checkpoint_dpi/flists/" << spec.name << ".f\",\n";
    out << "      \"boundary\": \"module_only_flist_contains_selected_dut_rtl_plus_probe; allowed_child_modules_are_generated_stubs_in_probe\",\n";
    write_string_array_json(out, "rtl_files", spec.rtl, "      ");
    out << ",\n";
    write_string_array_json(out, "module_only_flist_rtl", module_only_flist_rtl(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "composed_rtl_dependencies", composed_rtl_dependencies(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "allowed_child_modules", allowed_child_modules(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "child_stub_modules", child_stub_modules(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "forbidden_child_modules", forbidden_child_modules(spec), "      ");
    out << ",\n";
    out << "      \"probe_dut_instantiation_count\": " << probe_dut_instantiation_count(spec) << ",\n";
    out << "      \"checks\": [\n";
    out << "        {\"name\": \"dut_rtl_defines_top_module\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"allowed_child_modules_present_in_dut\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"allowed_child_modules_stubbed_in_probe\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"forbidden_child_modules_absent\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"probe_instantiates_exactly_one_dut\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"probe_instantiates_no_sibling_or_child_modules\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"flist_contains_only_selected_dut_rtl_plus_probe\", \"status\": \"pass\"}\n";
    out << "      ]\n";
    out << "    }" << (i + 1 == specs.size() ? "\n" : ",\n");
  }
  out << "  ]\n";
  out << "}\n";
  return out.str();
}

std::string cycle_contract_json(const std::vector<ModuleSpec>& specs) {
  std::ostringstream out;
  out << "{\n";
  out << "  \"schema\": \"e1-h1-full-checkpoint-cycle-contract-v0\",\n";
  out << "  \"generator\": \"e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp\",\n";
  out << "  \"readme_diagram\": \"e1/e1-h1/docs/modules/README.md#cycle-diagram\",\n";
  out << "  \"construction_rule\": \"every_generated_module_has_contiguous_named_cycle_phases_and_dpi_cycle_reporting\",\n";
  out << "  \"modules\": [\n";
  for (std::size_t i = 0; i < specs.size(); ++i) {
    const ModuleSpec& spec = specs[i];
    const std::vector<CycleStep> steps = cycle_steps(spec);
    out << "    {\n";
    out << "      \"name\": \"" << spec.name << "\",\n";
    out << "      \"top_module\": \"" << spec.top_module << "\",\n";
    out << "      \"probe_module\": \"" << spec.probe_module << "\",\n";
    out << "      \"readme_diagram\": \"e1/e1-h1/docs/modules/README.md#cycle-diagram\",\n";
    out << "      \"template\": \"" << cycle_template_name(spec) << "\",\n";
    out << "      \"cycle_period\": " << steps.size() << ",\n";
    out << "      \"primary_phase_signal\": \"" << primary_phase_signal(spec) << "\",\n";
    write_string_array_json(out, "phase_signals", cycle_phase_signals(spec), "      ");
    out << ",\n";
    write_phase_signal_trace_json(out, "expected_phase_signal_trace", spec, "      ");
    out << ",\n";
    write_cycle_steps_json(out, steps, "      ");
    out << ",\n";
    out << "      \"checks\": [\n";
    out << "        {\"name\": \"cycle_indices_contiguous\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"cycle_phase_signals_documented\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"dpi_probe_reports_cycles\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"dpi_probe_reports_named_phases\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"readme_cycle_diagram_declared\", \"status\": \"pass\"}\n";
    out << "      ]\n";
    out << "    }" << (i + 1 == specs.size() ? "\n" : ",\n");
  }
  out << "  ]\n";
  out << "}\n";
  return out.str();
}

std::string readme_index_row(const ModuleSpec& spec) {
  std::ostringstream out;
  out << "| `" << spec.name << "` | `" << cycle_template_name(spec) << "` | ";
  const std::vector<CycleStep> steps = cycle_steps(spec);
  for (std::size_t i = 0; i < steps.size(); ++i) {
    const CycleStep& step = steps[i];
    out << (i == 0 ? "" : "; ") << step.cycle << " `" << step.phase << "`";
  }
  out << " |";
  return out.str();
}

std::vector<std::string> required_readme_cycle_diagram_snippets() {
  return {
      "Full-checkpoint slot topology",
      "linear_tile_engine cycle 0 selects tile command metadata",
      "linear_tile_engine cycle 2 observes command handshake",
      "linear_tile_engine cycles 3..6 routes stream beats through ingress_sram latch buffer",
      "linear_tile_engine cycle 7 commits the tile and returns the separated modules to ready",
      "Tile cycle  control_cpu responsibility        ingress_sram latch buffer      systolic_array responsibility",
      "Control cycle  control_cpu responsibility",
      "Graph cycle  control_cpu responsibility",
      "Top cycle  graph_sequencer responsibility       selected slot engine",
      "The linear slot engine instantiates the separated `ingress_sram` latch buffer",
      "The control slot engine does not instantiate",
      "## Module-Only Boundary Matrix",
      "`control_cpu` | `e1_h1_control_cpu` plus generated per-module `imp1` reference and probe",
      "`ingress_sram` latch buffer | `e1_h1_stream_sram` plus generated per-module `imp1` reference and probe",
      "`systolic_array` | `e1_h1_systolic_array` plus generated per-module `imp1` reference and probe",
      "Generated full-checkpoint module | Selected generated RTL plus its probe only",
  };
}

void validate_readme_cycle_coverage(const fs::path& repo_root,
                                    const std::vector<ModuleSpec>& specs) {
  const fs::path readme_path = repo_root / "e1/e1-h1/docs/modules/README.md";
  const std::string readme = read_text(readme_path);
  require_contains(readme, "## Cycle Diagram", readme_path);
  require_contains(readme, "### Generated Cycle Contract Index", readme_path);
  for (const std::string& snippet : required_readme_cycle_diagram_snippets()) {
    require_contains(readme, snippet, readme_path);
  }
  for (const ModuleSpec& spec : specs) {
    require_contains(readme, spec.name, readme_path);
    require_contains(readme, cycle_template_name(spec), readme_path);
    require_contains(readme, readme_index_row(spec), readme_path);
    for (const CycleStep& step : cycle_steps(spec)) {
      require_contains(readme, step.phase, readme_path);
    }
  }
}

std::string readme_cycle_coverage_json(const std::vector<ModuleSpec>& specs) {
  std::ostringstream out;
  out << "{\n";
  out << "  \"schema\": \"e1-h1-full-checkpoint-readme-cycle-coverage-v0\",\n";
  out << "  \"generator\": \"e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp\",\n";
  out << "  \"readme\": \"e1/e1-h1/docs/modules/README.md\",\n";
  out << "  \"readme_diagram\": \"e1/e1-h1/docs/modules/README.md#cycle-diagram\",\n";
  out << "  \"readme_index\": \"e1/e1-h1/docs/modules/README.md#generated-cycle-contract-index\",\n";
  out << "  \"cycle_contract\": \"e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json\",\n";
  out << "  \"construction_rule\": \"every_generated_full_checkpoint_cycle_template_and_phase_name_is_present_in_the_module_readme\",\n";
  write_string_array_json(out, "required_cycle_diagram_snippets", required_readme_cycle_diagram_snippets(), "  ");
  out << ",\n";
  out << "  \"diagram_checks\": [\n";
  const std::vector<std::string> snippets = required_readme_cycle_diagram_snippets();
  for (std::size_t i = 0; i < snippets.size(); ++i) {
    out << "    {\"name\": \"readme_required_cycle_diagram_snippet_present\", \"snippet\": \""
        << snippets[i] << "\", \"status\": \"pass\"}"
        << (i + 1 == snippets.size() ? "\n" : ",\n");
  }
  out << "  ],\n";
  out << "  \"modules\": [\n";
  for (std::size_t i = 0; i < specs.size(); ++i) {
    const ModuleSpec& spec = specs[i];
    const std::vector<CycleStep> steps = cycle_steps(spec);
    std::vector<std::string> phase_names;
    for (const CycleStep& step : steps) {
      phase_names.push_back(step.phase);
    }
    out << "    {\n";
    out << "      \"name\": \"" << spec.name << "\",\n";
    out << "      \"top_module\": \"" << spec.top_module << "\",\n";
    out << "      \"template\": \"" << cycle_template_name(spec) << "\",\n";
    out << "      \"cycle_period\": " << steps.size() << ",\n";
    out << "      \"readme_diagram\": \"e1/e1-h1/docs/modules/README.md#cycle-diagram\",\n";
    out << "      \"readme_index\": \"e1/e1-h1/docs/modules/README.md#generated-cycle-contract-index\",\n";
    out << "      \"readme_index_row\": \"" << readme_index_row(spec) << "\",\n";
    write_string_array_json(out, "phase_names", phase_names, "      ");
    out << ",\n";
    out << "      \"checks\": [\n";
    out << "        {\"name\": \"readme_cycle_diagram_present\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"readme_cycle_contract_index_present\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"readme_module_name_present\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"readme_template_present\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"readme_exact_cycle_contract_row_present\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"readme_all_phase_names_present\", \"status\": \"pass\"}\n";
    out << "      ]\n";
    out << "    }" << (i + 1 == specs.size() ? "\n" : ",\n");
  }
  out << "  ]\n";
  out << "}\n";
  return out.str();
}

std::vector<std::string> verilator_fixed_args() {
  return {
      "--cc",
      "--exe",
      "--build",
      "--sv",
      "-Wall",
      "-Wno-DECLFILENAME",
      "-Wno-UNUSEDSIGNAL",
      "-Wno-UNUSEDPARAM",
      "-Wno-WIDTHEXPAND",
      "--timing",
  };
}

std::vector<std::string> expected_stdout_markers(const ModuleSpec& spec) {
  std::vector<std::string> markers = {
      "module=" + spec.name,
      "E1_H1_FULL_MODULE_DPI_CYCLE",
      "E1_H1_FULL_MODULE_DPI_PHASE_SIGNAL",
  };
  for (const CycleStep& step : cycle_steps(spec)) {
    markers.push_back("phase=" + step.phase);
  }
  return markers;
}

std::vector<std::string> expected_phase_trace_keys(const ModuleSpec& spec) {
  std::vector<std::string> keys;
  for (const CycleStep& step : cycle_steps(spec)) {
    keys.push_back(std::to_string(step.cycle) + ":" + step.phase);
  }
  return keys;
}

std::vector<std::string> expected_phase_signal_trace_keys(const ModuleSpec& spec) {
  std::vector<std::string> keys;
  const std::string signal = primary_phase_signal(spec);
  for (const CycleStep& step : cycle_steps(spec)) {
    const std::string expected = std::to_string(step.cycle);
    keys.push_back(std::to_string(step.cycle) + ":" + signal + ":" + expected + ":" + expected);
  }
  return keys;
}

std::string flist_path(const ModuleSpec& spec) {
  return "e1/e1-h1/generated/full_checkpoint_dpi/flists/" + spec.name + ".f";
}

std::string main_path(const ModuleSpec& spec) {
  return "e1/e1-h1/generated/full_checkpoint_dpi/" + spec.probe_module + "_main.cpp";
}

std::string probe_path(const ModuleSpec& spec) {
  return "e1/e1-h1/generated/full_checkpoint_dpi/" + spec.probe_module + ".sv";
}

std::string scoreboard_path() {
  return "e1/e1-h1/generated/full_checkpoint_dpi/e1_h1_full_checkpoint_module_dpi_scoreboard.cpp";
}

std::string verilator_launcher_path() {
  return "e1/e1-h1/generated/full_checkpoint_dpi/e1_h1_full_checkpoint_module_dpi_verilator_launcher.cpp";
}

std::string construction_ledger_path() {
  return "e1/e1-h1/generated/full_checkpoint_dpi/construction_ledger.json";
}

std::string obj_dir_name(const std::string& suite, const ModuleSpec& spec) {
  return "obj_" + suite + "_" + spec.name;
}

std::string recipe_run_executable(const std::string& suite, const ModuleSpec& spec) {
  return "<build-root>/" + obj_dir_name(suite, spec) + "/V" + spec.probe_module;
}

std::vector<std::string> recipe_build_command(const ModuleSpec& spec, const std::string& suite) {
  std::vector<std::string> command = {"verilator"};
  const std::vector<std::string> fixed_args = verilator_fixed_args();
  command.insert(command.end(), fixed_args.begin(), fixed_args.end());
  command.push_back("--top-module");
  command.push_back(spec.probe_module);
  command.push_back("-Mdir");
  command.push_back("<build-root>/" + obj_dir_name(suite, spec));
  command.push_back("-f");
  command.push_back(flist_path(spec));
  command.push_back(scoreboard_path());
  command.push_back(main_path(spec));
  return command;
}

std::string json_string(const std::string& value) {
  std::ostringstream out;
  out << "\"";
  for (char c : value) {
    if (c == '\\') {
      out << "\\\\";
    } else if (c == '"') {
      out << "\\\"";
    } else if (c == '\n') {
      out << "\\n";
    } else {
      out << c;
    }
  }
  out << "\"";
  return out.str();
}

std::string json_array(const std::vector<std::string>& values) {
  std::ostringstream out;
  out << "[";
  for (std::size_t i = 0; i < values.size(); ++i) {
    out << (i == 0 ? "" : ", ") << json_string(values[i]);
  }
  out << "]";
  return out.str();
}

std::string cpp_string_literal(const std::string& value) {
  std::ostringstream out;
  out << "\"";
  for (char c : value) {
    if (c == '\\') {
      out << "\\\\";
    } else if (c == '"') {
      out << "\\\"";
    } else if (c == '\n') {
      out << "\\n";
    } else {
      out << c;
    }
  }
  out << "\"";
  return out.str();
}

std::string cpp_string_array_literal(const std::vector<std::string>& values) {
  std::ostringstream out;
  out << "std::vector<std::string>{";
  for (std::size_t i = 0; i < values.size(); ++i) {
    out << (i == 0 ? "" : ", ") << cpp_string_literal(values[i]);
  }
  out << "}";
  return out.str();
}

std::string launcher_module_record(const ModuleSpec& spec, const std::string& suite) {
  std::ostringstream out;
  out << "{";
  out << "\"record\":\"module\",";
  out << "\"name\":" << json_string(spec.name) << ",";
  out << "\"scope\":\"generated_full_checkpoint_module_only\",";
  out << "\"top_module\":" << json_string(spec.probe_module) << ",";
  out << "\"dut_module\":" << json_string(spec.top_module) << ",";
  out << "\"flist\":" << json_string(flist_path(spec)) << ",";
  out << "\"scoreboard\":" << json_string(scoreboard_path()) << ",";
  out << "\"main\":" << json_string(main_path(spec)) << ",";
  out << "\"build_command\":" << json_array(recipe_build_command(spec, suite)) << ",";
  out << "\"run_executable\":" << json_string(recipe_run_executable(suite, spec)) << ",";
  out << "\"expected_stdout_markers\":" << json_array(expected_stdout_markers(spec));
  out << "}";
  return out.str();
}

std::string verilator_launcher_cpp(const std::vector<ModuleSpec>& specs) {
  const std::string suite = "full_checkpoint_module_dpi";
  std::ostringstream out;
  out << "// SPDX-License-Identifier: Apache-2.0\n";
  out << "// Generated by e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp.\n";
  out << "#include <cstdio>\n";
  out << "#include <cstdlib>\n";
  out << "#include <filesystem>\n";
  out << "#include <iostream>\n";
  out << "#include <sstream>\n";
  out << "#include <string>\n\n";
  out << "#include <vector>\n\n";
  out << "namespace fs = std::filesystem;\n\n";
  out << "std::string json_quote(const std::string& value) {\n";
  out << "  std::ostringstream out;\n";
  out << "  out << \"\\\"\";\n";
  out << "  for (char c : value) {\n";
  out << "    if (c == '\\\\') out << \"\\\\\\\\\";\n";
  out << "    else if (c == '\\\"') out << \"\\\\\\\"\";\n";
  out << "    else if (c == '\\n') out << \"\\\\n\";\n";
  out << "    else out << c;\n";
  out << "  }\n";
  out << "  out << \"\\\"\";\n";
  out << "  return out.str();\n";
  out << "}\n\n";
  out << "std::string json_array(const std::vector<std::string>& values) {\n";
  out << "  std::ostringstream out;\n";
  out << "  out << \"[\";\n";
  out << "  for (std::size_t i = 0; i < values.size(); ++i) {\n";
  out << "    out << (i == 0 ? \"\" : \", \") << json_quote(values[i]);\n";
  out << "  }\n";
  out << "  out << \"]\";\n";
  out << "  return out.str();\n";
  out << "}\n\n";
  out << "std::string replace_build_root(std::string value, const std::string& build_root) {\n";
  out << "  const std::string marker = \"<build-root>\";\n";
  out << "  std::size_t pos = 0;\n";
  out << "  while ((pos = value.find(marker, pos)) != std::string::npos) {\n";
  out << "    value.replace(pos, marker.size(), build_root);\n";
  out << "    pos += build_root.size();\n";
  out << "  }\n";
  out << "  return value;\n";
  out << "}\n\n";
  out << "std::string shell_quote(const std::string& value) {\n";
  out << "  std::string out = \"'\";\n";
  out << "  for (char c : value) {\n";
  out << "    if (c == '\\'') out += \"'\\\\''\";\n";
  out << "    else out += c;\n";
  out << "  }\n";
  out << "  out += \"'\";\n";
  out << "  return out;\n";
  out << "}\n\n";
  out << "std::string shell_command(const std::vector<std::string>& command, const std::string& build_root) {\n";
  out << "  std::ostringstream out;\n";
  out << "  for (std::size_t i = 0; i < command.size(); ++i) {\n";
  out << "    out << (i == 0 ? \"\" : \" \") << shell_quote(replace_build_root(command[i], build_root));\n";
  out << "  }\n";
  out << "  return out.str();\n";
  out << "}\n\n";
  out << "int run_shell_quiet(const std::string& command) {\n";
  out << "  return std::system((command + \" > /dev/null 2>&1\").c_str());\n";
  out << "}\n\n";
  out << "struct CommandResult {\n";
  out << "  int status = 1;\n";
  out << "  std::string stdout_text;\n";
  out << "};\n\n";
  out << "CommandResult run_shell_capture(const std::string& command) {\n";
  out << "  CommandResult result;\n";
  out << "  FILE* pipe = ::popen((command + \" 2>&1\").c_str(), \"r\");\n";
  out << "  if (!pipe) {\n";
  out << "    result.stdout_text = \"popen failed\";\n";
  out << "    return result;\n";
  out << "  }\n";
  out << "  char buffer[4096];\n";
  out << "  while (std::fgets(buffer, sizeof(buffer), pipe) != nullptr) {\n";
  out << "    result.stdout_text += buffer;\n";
  out << "  }\n";
  out << "  result.status = ::pclose(pipe);\n";
  out << "  return result;\n";
  out << "}\n\n";
  out << "std::vector<std::string> missing_markers(const std::string& stdout_text,\n";
  out << "                                         const std::vector<std::string>& expected_markers) {\n";
  out << "  std::vector<std::string> missing;\n";
  out << "  for (const std::string& marker : expected_markers) {\n";
  out << "    if (stdout_text.find(marker) == std::string::npos) {\n";
  out << "      missing.push_back(marker);\n";
  out << "    }\n";
  out << "  }\n";
  out << "  return missing;\n";
  out << "}\n\n";
  out << "std::size_t line_count(const std::string& value) {\n";
  out << "  if (value.empty()) return 0;\n";
  out << "  std::size_t count = 0;\n";
  out << "  for (char c : value) {\n";
  out << "    if (c == '\\n') ++count;\n";
  out << "  }\n";
  out << "  return value.back() == '\\n' ? count : count + 1;\n";
  out << "}\n\n";
  out << "std::string field_value(const std::string& line, const std::string& key) {\n";
  out << "  const std::string prefix = key + \"=\";\n";
  out << "  const std::size_t pos = line.find(prefix);\n";
  out << "  if (pos == std::string::npos) return \"\";\n";
  out << "  const std::size_t value_begin = pos + prefix.size();\n";
  out << "  const std::size_t value_end = line.find_first_of(\" \\t\\r\\n\", value_begin);\n";
  out << "  if (value_end == std::string::npos) return line.substr(value_begin);\n";
  out << "  return line.substr(value_begin, value_end - value_begin);\n";
  out << "}\n\n";
  out << "std::vector<std::string> vector_prefix(const std::vector<std::string>& values, std::size_t count) {\n";
  out << "  std::vector<std::string> prefix;\n";
  out << "  for (std::size_t i = 0; i < values.size() && i < count; ++i) {\n";
  out << "    prefix.push_back(values[i]);\n";
  out << "  }\n";
  out << "  return prefix;\n";
  out << "}\n\n";
  out << "std::vector<std::string> observed_phase_trace_keys(const std::string& stdout_text,\n";
  out << "                                                   const std::string& module_name) {\n";
  out << "  std::vector<std::string> keys;\n";
  out << "  std::istringstream lines(stdout_text);\n";
  out << "  std::string line;\n";
  out << "  while (std::getline(lines, line)) {\n";
  out << "    if (line.find(\"_DPI_CYCLE\") == std::string::npos) continue;\n";
  out << "    if (field_value(line, \"module\") != module_name) continue;\n";
  out << "    const std::string cycle = field_value(line, \"cycle\");\n";
  out << "    const std::string phase = field_value(line, \"phase\");\n";
  out << "    if (cycle.empty() || phase.empty()) continue;\n";
  out << "    keys.push_back(cycle + \":\" + phase);\n";
  out << "  }\n";
  out << "  return keys;\n";
  out << "}\n\n";
  out << "std::vector<std::string> observed_phase_signal_trace_keys(const std::string& stdout_text,\n";
  out << "                                                          const std::string& module_name) {\n";
  out << "  std::vector<std::string> keys;\n";
  out << "  std::istringstream lines(stdout_text);\n";
  out << "  std::string line;\n";
  out << "  while (std::getline(lines, line)) {\n";
  out << "    if (line.find(\"_DPI_PHASE_SIGNAL\") == std::string::npos) continue;\n";
  out << "    if (field_value(line, \"module\") != module_name) continue;\n";
  out << "    const std::string cycle = field_value(line, \"cycle\");\n";
  out << "    const std::string signal = field_value(line, \"signal\");\n";
  out << "    const std::string expected = field_value(line, \"expected\");\n";
  out << "    const std::string actual = field_value(line, \"actual\");\n";
  out << "    if (cycle.empty() || signal.empty() || expected.empty() || actual.empty()) continue;\n";
  out << "    keys.push_back(cycle + \":\" + signal + \":\" + expected + \":\" + actual);\n";
  out << "  }\n";
  out << "  return keys;\n";
  out << "}\n\n";
  out << "std::string normalized_cycle_key(const std::string& key, std::size_t period) {\n";
  out << "  const std::size_t delimiter = key.find(':');\n";
  out << "  if (delimiter == std::string::npos || period == 0) return key;\n";
  out << "  try {\n";
  out << "    const int cycle = std::stoi(key.substr(0, delimiter));\n";
  out << "    const int normalized = cycle % static_cast<int>(period);\n";
  out << "    return std::to_string(normalized) + key.substr(delimiter);\n";
  out << "  } catch (...) {\n";
  out << "    return key;\n";
  out << "  }\n";
  out << "}\n\n";
  out << "bool observed_trace_repeats_template(const std::vector<std::string>& observed,\n";
  out << "                                     const std::vector<std::string>& expected) {\n";
  out << "  if (expected.empty() || observed.size() < expected.size()) return false;\n";
  out << "  for (std::size_t i = 0; i < observed.size(); ++i) {\n";
  out << "    if (normalized_cycle_key(observed[i], expected.size()) != expected[i % expected.size()]) return false;\n";
  out << "  }\n";
  out << "  return true;\n";
  out << "}\n\n";
  out << "int run_module(const std::string& name,\n";
  out << "               const std::vector<std::string>& build_command,\n";
  out << "               const std::string& run_executable,\n";
  out << "               const std::vector<std::string>& expected_stdout_markers,\n";
  out << "               const std::vector<std::string>& expected_phase_trace_keys,\n";
  out << "               const std::vector<std::string>& expected_phase_signal_trace_keys,\n";
  out << "               const std::string& build_root) {\n";
  out << "  const int build_status = run_shell_quiet(shell_command(build_command, build_root));\n";
  out << "  int run_status = 1;\n";
  out << "  CommandResult run_result;\n";
  out << "  if (build_status == 0) {\n";
  out << "    run_result = run_shell_capture(shell_quote(replace_build_root(run_executable, build_root)));\n";
  out << "    run_status = run_result.status;\n";
  out << "  }\n";
  out << "  const std::vector<std::string> missing = missing_markers(run_result.stdout_text, expected_stdout_markers);\n";
  out << "  const bool markers_present = missing.empty();\n";
  out << "  const std::size_t observed_marker_count = expected_stdout_markers.size() - missing.size();\n";
  out << "  const std::vector<std::string> observed_phase_keys = observed_phase_trace_keys(run_result.stdout_text, name);\n";
  out << "  const std::vector<std::string> observed_phase_prefix = vector_prefix(observed_phase_keys, expected_phase_trace_keys.size());\n";
  out << "  const bool phase_trace_in_order = observed_phase_prefix == expected_phase_trace_keys && observed_phase_keys.size() >= expected_phase_trace_keys.size();\n";
  out << "  const bool phase_trace_repeats_template = observed_trace_repeats_template(observed_phase_keys, expected_phase_trace_keys);\n";
  out << "  const std::vector<std::string> observed_signal_keys = observed_phase_signal_trace_keys(run_result.stdout_text, name);\n";
  out << "  const std::vector<std::string> observed_signal_prefix = vector_prefix(observed_signal_keys, expected_phase_signal_trace_keys.size());\n";
  out << "  const bool phase_signal_trace_matches = observed_signal_prefix == expected_phase_signal_trace_keys && observed_signal_keys.size() >= expected_phase_signal_trace_keys.size();\n";
  out << "  const bool phase_signal_trace_repeats_template = observed_trace_repeats_template(observed_signal_keys, expected_phase_signal_trace_keys);\n";
  out << "  const bool passed = build_status == 0 && run_status == 0 && markers_present && phase_trace_in_order && phase_trace_repeats_template && phase_signal_trace_matches && phase_signal_trace_repeats_template;\n";
  out << "  std::cout << \"{\\\"record\\\":\\\"result\\\",\\\"name\\\":\" << json_quote(name)\n";
  out << "            << \",\\\"status\\\":\\\"\" << (passed ? \"pass\" : \"fail\") << \"\\\"\"\n";
  out << "            << \",\\\"build_status\\\":\" << build_status\n";
  out << "            << \",\\\"run_status\\\":\" << run_status\n";
  out << "            << \",\\\"build_command\\\":\" << json_array(build_command)\n";
  out << "            << \",\\\"run_executable\\\":\" << json_quote(run_executable)\n";
  out << "            << \",\\\"expected_stdout_markers\\\":\" << json_array(expected_stdout_markers)\n";
  out << "            << \",\\\"observed_stdout_marker_count\\\":\" << observed_marker_count\n";
  out << "            << \",\\\"missing_stdout_markers\\\":\" << json_array(missing)\n";
  out << "            << \",\\\"stdout_markers_present\\\":\" << (markers_present ? \"true\" : \"false\")\n";
  out << "            << \",\\\"expected_phase_trace_keys\\\":\" << json_array(expected_phase_trace_keys)\n";
  out << "            << \",\\\"observed_phase_trace_prefix_keys\\\":\" << json_array(observed_phase_prefix)\n";
  out << "            << \",\\\"observed_phase_trace_count\\\":\" << observed_phase_keys.size()\n";
  out << "            << \",\\\"phase_trace_in_order\\\":\" << (phase_trace_in_order ? \"true\" : \"false\")\n";
  out << "            << \",\\\"phase_trace_repeats_template\\\":\" << (phase_trace_repeats_template ? \"true\" : \"false\")\n";
  out << "            << \",\\\"expected_phase_signal_trace_keys\\\":\" << json_array(expected_phase_signal_trace_keys)\n";
  out << "            << \",\\\"observed_phase_signal_trace_prefix_keys\\\":\" << json_array(observed_signal_prefix)\n";
  out << "            << \",\\\"observed_phase_signal_trace_count\\\":\" << observed_signal_keys.size()\n";
  out << "            << \",\\\"phase_signal_trace_matches\\\":\" << (phase_signal_trace_matches ? \"true\" : \"false\")\n";
  out << "            << \",\\\"phase_signal_trace_repeats_template\\\":\" << (phase_signal_trace_repeats_template ? \"true\" : \"false\")\n";
  out << "            << \",\\\"captured_stdout_line_count\\\":\" << line_count(run_result.stdout_text)\n";
  out << "            << \"}\\n\";\n";
  out << "  return passed ? 0 : 1;\n";
  out << "}\n\n";
  out << "int main(int argc, char** argv) {\n";
  out << "  bool dry_run = false;\n";
  out << "  bool run = false;\n";
  out << "  std::string build_root = \"build/full_checkpoint_module_dpi_cpp_verilator_launcher\";\n";
  out << "  for (int i = 1; i < argc; ++i) {\n";
  out << "    const std::string arg(argv[i]);\n";
  out << "    if (arg == \"--dry-run\") {\n";
  out << "      dry_run = true;\n";
  out << "    } else if (arg == \"--run\") {\n";
  out << "      run = true;\n";
  out << "    } else if (arg == \"--build-root\" && i + 1 < argc) {\n";
  out << "      build_root = argv[++i];\n";
  out << "    }\n";
  out << "  }\n";
  out << "  if (dry_run) {\n";
  out << "  std::cout << "
      << cpp_string_literal(
             "{\"record\":\"suite\",\"schema\":\"e1-h1-full-checkpoint-module-dpi-verilator-launcher-v0\","
             "\"suite\":\"full_checkpoint_module_dpi\",\"module_count\":" +
             std::to_string(specs.size()) +
             ",\"construction_rule\":\"cpp_generated_launcher_enumerates_each_generated_module_only_verilator_run\"}")
      << " << \"\\n\";\n";
  for (const ModuleSpec& spec : specs) {
    out << "  std::cout << " << cpp_string_literal(launcher_module_record(spec, suite))
        << " << \"\\n\";\n";
  }
  out << "    return 0;\n";
  out << "  }\n";
  out << "  if (!run) {\n";
  out << "    std::cerr << \"usage: e1_h1_full_checkpoint_module_dpi_verilator_launcher --dry-run | --run --build-root <dir>\\\\n\";\n";
  out << "    return 2;\n";
  out << "  }\n";
  out << "  fs::create_directories(build_root);\n";
  out << "  int failures = 0;\n";
  for (const ModuleSpec& spec : specs) {
    out << "  failures += run_module("
        << cpp_string_literal(spec.name) << ", "
        << cpp_string_array_literal(recipe_build_command(spec, suite)) << ", "
        << cpp_string_literal(recipe_run_executable(suite, spec)) << ", "
        << cpp_string_array_literal(expected_stdout_markers(spec)) << ", "
        << cpp_string_array_literal(expected_phase_trace_keys(spec)) << ", "
        << cpp_string_array_literal(expected_phase_signal_trace_keys(spec)) << ", build_root);\n";
  }
  out << "  std::cout << \"{\\\"record\\\":\\\"run_summary\\\",\\\"suite\\\":\\\"full_checkpoint_module_dpi\\\",\\\"module_count\\\":"
      << specs.size()
      << ",\\\"failures\\\":\" << failures << \",\\\"status\\\":\\\"\" << (failures == 0 ? \"pass\" : \"fail\") << \"\\\"}\\n\";\n";
  out << "  return failures == 0 ? 0 : 1;\n";
  out << "  return 0;\n";
  out << "}\n";
  return out.str();
}

void write_verilator_object_json(std::ostringstream& out,
                                 const ModuleSpec& spec,
                                 const std::string& indent) {
  out << indent << "\"verilator\": {\n";
  out << indent << "  \"top_module\": \"" << spec.probe_module << "\",\n";
  out << indent << "  \"dut_module\": \"" << spec.top_module << "\",\n";
  out << indent << "  \"flist\": \"" << flist_path(spec) << "\",\n";
  out << indent << "  \"scoreboard\": \"" << scoreboard_path() << "\",\n";
  out << indent << "  \"main\": \"" << main_path(spec) << "\",\n";
  out << indent << "  \"obj_dir_placeholder\": \"<obj_dir>\",\n";
  out << indent << "  \"run_executable\": \"V" << spec.probe_module << "\",\n";
  out << indent << "  \"primary_phase_signal\": \"" << primary_phase_signal(spec) << "\",\n";
  write_string_array_json(out, "fixed_args", verilator_fixed_args(), indent + "  ");
  out << ",\n";
  write_string_array_json(out, "expected_stdout_markers", expected_stdout_markers(spec), indent + "  ");
  out << ",\n";
  write_phase_signal_trace_json(out, "expected_phase_signal_trace", spec, indent + "  ");
  out << "\n" << indent << "}";
}

std::string verilator_execution_recipe_json(const std::vector<ModuleSpec>& specs) {
  const std::string suite = "full_checkpoint_module_dpi";
  std::ostringstream out;
  out << "{\n";
  out << "  \"schema\": \"e1-h1-full-checkpoint-module-dpi-verilator-execution-recipe-v0\",\n";
  out << "  \"generator\": \"e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp\",\n";
  out << "  \"runner\": \"e1/tools/run_module_dpi_verilator.py\",\n";
  out << "  \"suite\": \"" << suite << "\",\n";
  out << "  \"test_plan\": \"e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json\",\n";
  out << "  \"report\": \"e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_report.json\",\n";
  out << "  \"construction_rule\": \"cpp_generator_owns_exact_generated_module_only_verilator_build_and_run_recipe\",\n";
  out << "  \"modules\": [\n";
  for (std::size_t i = 0; i < specs.size(); ++i) {
    const ModuleSpec& spec = specs[i];
    out << "    {\n";
    out << "      \"name\": \"" << spec.name << "\",\n";
    out << "      \"scope\": \"generated_full_checkpoint_module_only\",\n";
    out << "      \"top_module\": \"" << spec.probe_module << "\",\n";
    out << "      \"dut_module\": \"" << spec.top_module << "\",\n";
    out << "      \"flist\": \"" << flist_path(spec) << "\",\n";
    out << "      \"scoreboard\": \"" << scoreboard_path() << "\",\n";
    out << "      \"main\": \"" << main_path(spec) << "\",\n";
    write_string_array_json(out, "build_command", recipe_build_command(spec, suite), "      ");
    out << ",\n";
    out << "      \"run_executable\": \"" << recipe_run_executable(suite, spec) << "\",\n";
    write_string_array_json(out, "expected_stdout_markers", expected_stdout_markers(spec), "      ");
    out << ",\n";
    out << "      \"primary_phase_signal\": \"" << primary_phase_signal(spec) << "\",\n";
    write_phase_signal_trace_json(out, "expected_phase_signal_trace", spec, "      ");
    out << "\n";
    out << "    }" << (i + 1 == specs.size() ? "\n" : ",\n");
  }
  out << "  ]\n";
  out << "}\n";
  return out.str();
}

std::string module_test_plan_json(const std::vector<ModuleSpec>& specs) {
  std::ostringstream out;
  out << "{\n";
  out << "  \"schema\": \"e1-h1-full-checkpoint-module-dpi-test-plan-v0\",\n";
  out << "  \"generator\": \"e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp\",\n";
  out << "  \"runner\": \"verilator\",\n";
  out << "  \"construction_rule\": \"each_generated_full_checkpoint_module_has_a_generated_module_only_verilator_invocation\",\n";
  out << "  \"modules\": [\n";
  for (std::size_t i = 0; i < specs.size(); ++i) {
    const ModuleSpec& spec = specs[i];
    out << "    {\n";
    out << "      \"name\": \"" << spec.name << "\",\n";
    out << "      \"scope\": \"generated_full_checkpoint_module_only\",\n";
    out << "      \"probe\": \"e1/e1-h1/generated/full_checkpoint_dpi/" << spec.probe_module << ".sv\",\n";
    out << "      \"primary_phase_signal\": \"" << primary_phase_signal(spec) << "\",\n";
    write_phase_signal_trace_json(out, "expected_phase_signal_trace", spec, "      ");
    out << ",\n";
    write_verilator_object_json(out, spec, "      ");
    out << ",\n";
    out << "      \"checks\": [\n";
    out << "        {\"name\": \"probe_exists\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"flist_exists\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"scoreboard_exists\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"main_exists\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"verilator_top_is_probe_module\", \"status\": \"pass\"}\n";
    out << "      ]\n";
    out << "    }" << (i + 1 == specs.size() ? "\n" : ",\n");
  }
  out << "  ]\n";
  out << "}\n";
  return out.str();
}

std::vector<std::string> phase_names(const ModuleSpec& spec) {
  std::vector<std::string> phases;
  for (const CycleStep& step : cycle_steps(spec)) {
    phases.push_back(step.phase);
  }
  return phases;
}

std::string construction_ledger_json(const std::vector<ModuleSpec>& specs) {
  std::ostringstream out;
  out << "{\n";
  out << "  \"schema\": \"e1-h1-full-checkpoint-module-dpi-construction-ledger-v0\",\n";
  out << "  \"generator\": \"e1/e1-h1/tools/generate_full_checkpoint_module_dpi.cpp\",\n";
  out << "  \"construction_rule\": \"cpp_full_checkpoint_module_specs_are_the_single_source_for_probe_flist_interfaces_cycles_readme_and_verilator_recipe\",\n";
  out << "  \"manifest\": \"e1/e1-h1/generated/full_checkpoint_dpi/manifest.json\",\n";
  out << "  \"module_interfaces_doc\": \"e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md\",\n";
  out << "  \"module_isolation_proof\": \"e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json\",\n";
  out << "  \"cycle_contract\": \"e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json\",\n";
  out << "  \"module_test_plan\": \"e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json\",\n";
  out << "  \"verilator_execution_recipe\": \"e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json\",\n";
  out << "  \"verilator_execution_launcher\": \"" << verilator_launcher_path() << "\",\n";
  out << "  \"readme_cycle_coverage\": \"e1/e1-h1/generated/full_checkpoint_dpi/readme_cycle_coverage.json\",\n";
  out << "  \"modules\": [\n";
  for (std::size_t i = 0; i < specs.size(); ++i) {
    const ModuleSpec& spec = specs[i];
    const std::vector<CycleStep> steps = cycle_steps(spec);
    out << "    {\n";
    out << "      \"name\": \"" << spec.name << "\",\n";
    out << "      \"source_record\": \"module_specs:" << spec.name << "\",\n";
    out << "      \"top_module\": \"" << spec.top_module << "\",\n";
    out << "      \"probe_module\": \"" << spec.probe_module << "\",\n";
    out << "      \"probe\": \"" << probe_path(spec) << "\",\n";
    out << "      \"main\": \"" << main_path(spec) << "\",\n";
    out << "      \"flist\": \"" << flist_path(spec) << "\",\n";
    out << "      \"scoreboard\": \"" << scoreboard_path() << "\",\n";
    write_string_array_json(out, "rtl", module_only_flist_rtl(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "composed_rtl_dependencies", composed_rtl_dependencies(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "child_stub_modules", child_stub_modules(spec), "      ");
    out << ",\n";
    write_string_array_json(out, "allowed_child_modules", allowed_child_modules(spec), "      ");
    out << ",\n";
    out << "      \"probe_dut_instantiation_count\": " << probe_dut_instantiation_count(spec) << ",\n";
    out << "      \"input_signal_count\": " << spec.input_signals.size() << ",\n";
    out << "      \"output_signal_count\": " << spec.output_signals.size() << ",\n";
    out << "      \"cycle_template\": \"" << cycle_template_name(spec) << "\",\n";
    out << "      \"cycle_period\": " << steps.size() << ",\n";
    out << "      \"primary_phase_signal\": \"" << primary_phase_signal(spec) << "\",\n";
    write_string_array_json(out, "phase_signals", cycle_phase_signals(spec), "      ");
    out << ",\n";
    write_phase_signal_trace_json(out, "expected_phase_signal_trace", spec, "      ");
    out << ",\n";
    write_string_array_json(out, "phase_names", phase_names(spec), "      ");
    out << ",\n";
    write_string_array_json(
        out,
        "derived_artifacts",
        {
            probe_path(spec),
            main_path(spec),
            flist_path(spec),
            scoreboard_path(),
            "e1/e1-h1/generated/full_checkpoint_dpi/module_interfaces.md",
            "e1/e1-h1/generated/full_checkpoint_dpi/module_isolation.json",
            "e1/e1-h1/generated/full_checkpoint_dpi/cycle_contract.json",
            "e1/e1-h1/generated/full_checkpoint_dpi/module_test_plan.json",
            "e1/e1-h1/generated/full_checkpoint_dpi/verilator_execution_recipe.json",
            verilator_launcher_path(),
            "e1/e1-h1/generated/full_checkpoint_dpi/readme_cycle_coverage.json",
        },
        "      ");
    out << ",\n";
    out << "      \"checks\": [\n";
    out << "        {\"name\": \"probe_emitted_from_cpp_spec\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"flist_emitted_from_cpp_spec\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"interface_doc_emitted_from_cpp_spec\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"cycle_contract_emitted_from_cpp_spec\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"readme_cycle_coverage_validated_from_cpp_spec\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"verilator_recipe_emitted_from_cpp_spec\", \"status\": \"pass\"},\n";
    out << "        {\"name\": \"verilator_launcher_emitted_from_cpp_spec\", \"status\": \"pass\"}\n";
    out << "      ]\n";
    out << "    }" << (i + 1 == specs.size() ? "\n" : ",\n");
  }
  out << "  ]\n";
  out << "}\n";
  return out.str();
}

std::string probe_header(const std::string& module_name) {
  std::ostringstream out;
  out << "`default_nettype none\n\n";
  out << "module e1_h1_full_checkpoint_module_dpi_" << module_name << ";\n";
  out << "  import \"DPI-C\" function void e1_h1_full_dpi_begin(input string module_name, input string vip_case);\n";
  out << "  import \"DPI-C\" function void e1_h1_full_dpi_cycle(input string module_name, input int cycle, input string phase);\n";
  out << "  import \"DPI-C\" function int e1_h1_full_dpi_phase_signal(\n";
  out << "    input string module_name,\n";
  out << "    input string signal_name,\n";
  out << "    input int cycle,\n";
  out << "    input int expected,\n";
  out << "    input int actual\n";
  out << "  );\n";
  out << "  import \"DPI-C\" function int e1_h1_full_dpi_expect_u32(\n";
  out << "    input string module_name,\n";
  out << "    input string signal_name,\n";
  out << "    input int cycle,\n";
  out << "    input int expected,\n";
  out << "    input int actual\n";
  out << "  );\n\n";
  out << "  task automatic tick;\n";
  out << "    clk_i = 1'b0; #1;\n";
  out << "    clk_i = 1'b1; #1;\n";
  out << "  endtask\n\n";
  out << "  task automatic expect_u32(input string signal_name, input int cycle, input int expected, input logic [31:0] actual);\n";
  out << "    if (e1_h1_full_dpi_expect_u32(\"" << module_name << "\", signal_name, cycle, expected, int'(actual)) == 0) begin\n";
  out << "      $fatal(1, \"" << module_name << " mismatch %s\", signal_name);\n";
  out << "    end\n";
  out << "  endtask\n\n";
  out << "  task automatic expect_phase_signal(input string signal_name, input int cycle, input int expected, input int actual);\n";
  out << "    if (e1_h1_full_dpi_phase_signal(\"" << module_name << "\", signal_name, cycle, expected, actual) == 0) begin\n";
  out << "      $fatal(1, \"" << module_name << " phase signal mismatch %s\", signal_name);\n";
  out << "    end\n";
  out << "  endtask\n\n";
  out << "  int contract_cycle = 0;\n\n";
  return out.str();
}

std::string probe_footer() {
  return "endmodule\n\n`default_nettype wire\n";
}

std::string child_stub_sv(const std::string& child) {
  if (child == "e1_h1_stream_sram") {
    return R"sv(
`default_nettype none
module e1_h1_stream_sram (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        array_valid_o,
  input  logic        array_ready_i,
  output logic [63:0] array_data_o
);
  logic unused_inputs;
  assign unused_inputs = clk_i ^ rst_ni ^ stream_last_i ^ stream_error_i;
  assign stream_ready_o = array_ready_i;
  assign array_valid_o = stream_valid_i;
  assign array_data_o = stream_data_i;
endmodule

`default_nettype wire
)sv";
  }
  if (child == "e1_h1_systolic_array") {
    return R"sv(
`default_nettype none
module e1_h1_systolic_array (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        cmd_valid_i,
  output logic        cmd_ready_o,
  input  logic [31:0] cmd_input_addr_i,
  input  logic [31:0] cmd_weight_addr_i,
  input  logic [31:0] cmd_output_addr_i,
  input  logic [15:0] cmd_rows_i,
  input  logic [15:0] cmd_cols_i,
  input  logic [15:0] cmd_depth_i,
  input  logic        input_valid_i,
  output logic        input_ready_o,
  input  logic [63:0] input_data_i,
  output logic        done_o,
  output logic        error_o,
  output logic        debug_busy_o,
  output logic [31:0] result_digest_o
);
  logic [3:0] done_shift_q;
  logic [31:0] digest_q;
  logic unused_payload;
  assign cmd_ready_o = 1'b1;
  assign input_ready_o = 1'b1;
  assign done_o = done_shift_q[3];
  assign error_o = 1'b0;
  assign debug_busy_o = |done_shift_q;
  assign result_digest_o = digest_q;
  assign unused_payload = ^{
      cmd_input_addr_i,
      cmd_weight_addr_i,
      cmd_output_addr_i,
      cmd_rows_i,
      cmd_cols_i,
      cmd_depth_i,
      input_valid_i,
      input_data_i
  };
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      done_shift_q <= 4'b0000;
      digest_q <= '0;
    end else begin
      done_shift_q <= {done_shift_q[2:0], cmd_valid_i};
      if (cmd_valid_i) begin
        digest_q <= cmd_input_addr_i
            ^ cmd_weight_addr_i
            ^ cmd_output_addr_i
            ^ {cmd_rows_i, cmd_cols_i}
            ^ {16'd0, cmd_depth_i};
      end else if (input_valid_i) begin
        digest_q <= {digest_q[30:0], digest_q[31]}
            ^ input_data_i[31:0]
            ^ input_data_i[63:32];
      end
    end
  end
endmodule

`default_nettype wire
)sv";
  }
  if (child == "e1_h1_tinyllama_linear_scheduler") {
    return R"sv(
`default_nettype none
module e1_h1_tinyllama_linear_scheduler (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic        cmd_valid_o,
  input  logic        cmd_ready_i,
  output logic [31:0] cmd_input_addr_o,
  output logic [31:0] cmd_weight_addr_o,
  output logic [31:0] cmd_output_addr_o,
  output logic [15:0] cmd_rows_o,
  output logic [15:0] cmd_cols_o,
  output logic [15:0] cmd_depth_o,
  input  logic        array_done_i,
  input  logic        array_error_i,
  output logic [31:0] layer_o,
  output logic [2:0]  op_index_o,
  output logic [8:0]  input_tile_o,
  output logic [8:0]  output_tile_o,
  output logic [31:0] issued_commands_o,
  output logic [2:0]  cycle_phase_o
);
  typedef enum logic [1:0] {StateIdle, StateRun, StateDone, StateError} state_e;
  state_e state_q;
  logic [2:0] phase_q;
  logic [31:0] issued_q;
  assign busy_o = state_q == StateRun;
  assign done_o = state_q == StateDone;
  assign error_o = state_q == StateError;
  assign cmd_valid_o = state_q == StateRun && (phase_q == 3'd1 || phase_q == 3'd2);
  assign cmd_input_addr_o = 32'h0100_0000 + issued_q * 32'd64;
  assign cmd_weight_addr_o = 32'h1000_0000 + issued_q * 32'd64;
  assign cmd_output_addr_o = 32'h3000_0000 + issued_q * 32'd64;
  assign cmd_rows_o = 16'd16;
  assign cmd_cols_o = 16'd16;
  assign cmd_depth_o = 16'd16;
  assign layer_o = 32'd0;
  assign op_index_o = 3'd0;
  assign input_tile_o = issued_q[8:0];
  assign output_tile_o = 9'd0;
  assign issued_commands_o = issued_q;
  assign cycle_phase_o = phase_q;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      state_q <= StateIdle;
      phase_q <= 3'd0;
      issued_q <= 32'd0;
    end else begin
      unique case (state_q)
        StateIdle: begin
          if (start_i) begin
            state_q <= StateRun;
            phase_q <= 3'd0;
            issued_q <= 32'd0;
          end
        end
        StateRun: begin
          if (phase_q == 3'd2 && cmd_ready_i) begin
            issued_q <= issued_q + 32'd1;
          end
          if (phase_q == 3'd6 && array_error_i) begin
            state_q <= StateError;
          end else if (phase_q == 3'd6 && !array_done_i) begin
            phase_q <= 3'd6;
          end else if (phase_q == 3'd7) begin
            if (issued_q >= 32'd4) begin
              state_q <= StateDone;
            end else begin
              phase_q <= 3'd0;
            end
          end else begin
            phase_q <= phase_q + 3'd1;
          end
        end
        StateDone: begin
          if (!start_i) state_q <= StateIdle;
        end
        default: state_q <= StateIdle;
      endcase
    end
  end
endmodule

`default_nettype wire
)sv";
  }
  if (child == "e1_h1_tinyllama_linear_slot_engine") {
    return R"sv(
`default_nettype none
module e1_h1_tinyllama_linear_slot_engine #(
  parameter int unsigned SmokeMaxTilesPerLinearSlot = 0
) (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic [31:0] layer_i,
  input  logic [2:0]  op_index_i,
  input  logic        stream_valid_i,
  output logic        stream_ready_o,
  input  logic [63:0] stream_data_i,
  input  logic        stream_last_i,
  input  logic        stream_error_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        error_o,
  output logic [31:0] issued_commands_o,
  output logic [31:0] expected_commands_o,
  output logic [2:0]  cycle_phase_o,
  output logic [31:0] layer_o,
  output logic [2:0]  op_index_o,
  output logic [8:0]  input_tile_o,
  output logic [8:0]  output_tile_o,
  output logic        scheduler_cmd_valid_o,
  output logic        array_cmd_valid_o,
  output logic        array_cmd_ready_o,
  output logic [31:0] cmd_input_addr_o,
  output logic [31:0] cmd_weight_addr_o,
  output logic [31:0] cmd_output_addr_o,
  output logic [15:0] cmd_rows_o,
  output logic [15:0] cmd_cols_o,
  output logic [15:0] cmd_depth_o,
  output logic        buffer_array_valid_o,
  output logic        buffer_array_ready_o,
  output logic [63:0] buffer_array_data_o,
  output logic        array_done_o,
  output logic        array_debug_busy_o,
  output logic [31:0] array_result_digest_o
);
  logic done_q;
  logic unused_inputs;
  assign unused_inputs = ^{SmokeMaxTilesPerLinearSlot[0], stream_valid_i, stream_data_i,
                           stream_last_i, stream_error_i};
  assign stream_ready_o = 1'b1;
  assign busy_o = start_i && !done_q;
  assign done_o = done_q;
  assign error_o = 1'b0;
  assign issued_commands_o = done_q ? 32'd1 : 32'd0;
  assign expected_commands_o = 32'd1;
  assign cycle_phase_o = done_q ? 3'd6 : 3'd2;
  assign layer_o = layer_i;
  assign op_index_o = op_index_i;
  assign input_tile_o = 9'd0;
  assign output_tile_o = 9'd0;
  assign scheduler_cmd_valid_o = start_i;
  assign array_cmd_valid_o = start_i;
  assign array_cmd_ready_o = 1'b1;
  assign cmd_input_addr_o = 32'h0100_0000;
  assign cmd_weight_addr_o = 32'h1000_0000;
  assign cmd_output_addr_o = 32'h3000_0000;
  assign cmd_rows_o = 16'd16;
  assign cmd_cols_o = 16'd16;
  assign cmd_depth_o = 16'd16;
  assign buffer_array_valid_o = stream_valid_i;
  assign buffer_array_ready_o = 1'b1;
  assign buffer_array_data_o = stream_data_i;
  assign array_done_o = done_q;
  assign array_debug_busy_o = start_i && !done_q;
  assign array_result_digest_o = 32'h4100_0010 ^ {29'd0, op_index_i};
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      done_q <= 1'b0;
    end else begin
      done_q <= start_i;
    end
  end
endmodule

`default_nettype wire
)sv";
  }
  if (child == "e1_h1_tinyllama_control_slot_engine") {
    return R"sv(
`default_nettype none
module e1_h1_tinyllama_control_slot_engine (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  input  logic [31:0] layer_i,
  input  logic [2:0]  control_op_index_i,
  input  logic [3:0]  control_kind_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        control_valid_o,
  input  logic        control_ready_i,
  output logic        control_commit_o,
  output logic [31:0] layer_o,
  output logic [2:0]  control_op_index_o,
  output logic [3:0]  control_kind_o,
  output logic [31:0] issued_control_ops_o,
  output logic [1:0]  cycle_phase_o
);
  logic done_q;
  assign busy_o = start_i && !done_q;
  assign done_o = done_q;
  assign control_valid_o = start_i;
  assign control_commit_o = done_q;
  assign layer_o = layer_i;
  assign control_op_index_o = control_op_index_i;
  assign control_kind_o = control_kind_i;
  assign issued_control_ops_o = done_q ? 32'd1 : 32'd0;
  assign cycle_phase_o = done_q ? 2'd3 : 2'd0;
  logic unused_ready;
  assign unused_ready = control_ready_i;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      done_q <= 1'b0;
    end else begin
      done_q <= start_i;
    end
  end
endmodule

`default_nettype wire
)sv";
  }
  if (child == "e1_h1_tinyllama_graph_sequencer") {
    return R"sv(
`default_nettype none
module e1_h1_tinyllama_graph_sequencer (
  input  logic        clk_i,
  input  logic        rst_ni,
  input  logic        start_i,
  output logic        busy_o,
  output logic        done_o,
  output logic        slot_valid_o,
  input  logic        slot_ready_i,
  output logic        launch_control_o,
  output logic        launch_linear_o,
  input  logic        op_done_i,
  output logic [31:0] layer_o,
  output logic [3:0]  layer_slot_o,
  output logic [2:0]  linear_op_index_o,
  output logic [2:0]  control_op_index_o,
  output logic [3:0]  control_kind_o,
  output logic [31:0] linear_tile_count_o,
  output logic [31:0] issued_graph_slots_o,
  output logic [1:0]  cycle_phase_o
);
  logic [8:0] slot_q;
  logic [1:0] phase_q;
  logic running_q;
  wire is_linear = slot_q[0] == 1'b0;
  assign busy_o = running_q;
  assign done_o = slot_q >= 9'd308;
  assign slot_valid_o = running_q && phase_q == 2'd0;
  assign launch_linear_o = running_q && phase_q == 2'd1 && is_linear;
  assign launch_control_o = running_q && phase_q == 2'd1 && !is_linear;
  assign layer_o = {23'd0, slot_q} / 32'd14;
  assign layer_slot_o = slot_q[3:0];
  assign linear_op_index_o = slot_q[3:1] % 3'd7;
  assign control_op_index_o = slot_q[3:1] % 3'd7;
  assign control_kind_o = {1'b0, slot_q[3:1]} + 4'd1;
  assign linear_tile_count_o = is_linear ? 32'd1 : 32'd0;
  assign issued_graph_slots_o = {23'd0, slot_q};
  assign cycle_phase_o = phase_q;
  logic unused_inputs;
  assign unused_inputs = slot_ready_i ^ op_done_i;
  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      slot_q <= 9'd0;
      phase_q <= 2'd0;
      running_q <= 1'b0;
    end else begin
      if (start_i && !running_q && !done_o) begin
        running_q <= 1'b1;
        phase_q <= 2'd0;
      end else if (running_q) begin
        if (phase_q == 2'd3) begin
          slot_q <= slot_q + 9'd1;
          phase_q <= 2'd0;
          if (slot_q + 9'd1 >= 9'd308) begin
            running_q <= 1'b0;
          end
        end else begin
          phase_q <= phase_q + 2'd1;
        end
      end
    end
  end
endmodule

`default_nettype wire
)sv";
  }
  throw std::runtime_error("no generated child stub for " + child);
}

std::string child_stubs_sv(const ModuleSpec& spec) {
  std::ostringstream out;
  for (const std::string& child : child_stub_modules(spec)) {
    out << child_stub_sv(child);
  }
  return out.str();
}

std::string probe_sv_with_phase_signal_checks(const ModuleSpec& spec) {
  const std::vector<CycleStep> steps = cycle_steps(spec);
  const std::string marker =
      "e1_h1_full_dpi_cycle(\"" + spec.name + "\", cycle, phase_name(cycle));";
  const std::string check =
      "if (int'(" + primary_phase_signal(spec) + ") == (contract_cycle % " +
      std::to_string(steps.size()) + ")) begin\n"
      "        e1_h1_full_dpi_cycle(\"" + spec.name +
      "\", contract_cycle, phase_name(contract_cycle));\n"
      "        expect_phase_signal(\"" + primary_phase_signal(spec) +
      "\", contract_cycle, contract_cycle % " + std::to_string(steps.size()) +
      ", int'(" + primary_phase_signal(spec) + "));\n"
      "        contract_cycle++;\n"
      "      end";
  std::string text = spec.probe_sv;
  std::size_t pos = 0;
  int replacements = 0;
  while ((pos = text.find(marker, pos)) != std::string::npos) {
    text.replace(pos, marker.size(), check);
    pos += check.size();
    ++replacements;
  }
  if (replacements == 0) {
    throw std::runtime_error(spec.name + " probe has no generated phase marker to bind");
  }
  return text + child_stubs_sv(spec);
}

std::string sv_graph_sequencer() {
  std::ostringstream out;
  out << probe_header("graph_sequencer");
  out << R"sv(  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic busy_o;
  logic done_o;
  logic slot_valid_o;
  logic slot_ready_i;
  logic launch_control_o;
  logic launch_linear_o;
  logic op_done_i;
  logic [31:0] layer_o;
  logic [3:0] layer_slot_o;
  logic [2:0] linear_op_index_o;
  logic [2:0] control_op_index_o;
  logic [3:0] control_kind_o;
  logic [31:0] linear_tile_count_o;
  logic [31:0] issued_graph_slots_o;
  logic [1:0] cycle_phase_o;

  e1_h1_tinyllama_graph_sequencer u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .slot_valid_o(slot_valid_o),
    .slot_ready_i(slot_ready_i),
    .launch_control_o(launch_control_o),
    .launch_linear_o(launch_linear_o),
    .op_done_i(op_done_i),
    .layer_o(layer_o),
    .layer_slot_o(layer_slot_o),
    .linear_op_index_o(linear_op_index_o),
    .control_op_index_o(control_op_index_o),
    .control_kind_o(control_kind_o),
    .linear_tile_count_o(linear_tile_count_o),
    .issued_graph_slots_o(issued_graph_slots_o),
    .cycle_phase_o(cycle_phase_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 4)
      0: return "present_graph_slot";
      1: return "launch_selected_engine";
      2: return "wait_for_slot_done";
      3: return "commit_graph_slot";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    int linear_launches;
    int control_launches;
    e1_h1_full_dpi_begin("graph_sequencer", "ordered_308_slot_graph");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    slot_ready_i = 1'b1;
    op_done_i = 1'b0;
    linear_launches = 0;
    control_launches = 0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 1600 && !done_o; cycle++) begin
      e1_h1_full_dpi_cycle("graph_sequencer", cycle, phase_name(cycle));
      op_done_i = (cycle_phase_o == 2'd2);
      if (launch_linear_o) linear_launches++;
      if (launch_control_o) control_launches++;
      tick();
    end
    expect_u32("issued_graph_slots_o", 0, 308, issued_graph_slots_o);
    expect_u32("linear_launches", 0, 154, linear_launches[31:0]);
    expect_u32("control_launches", 0, 154, control_launches[31:0]);
    if (!done_o) $fatal(1, "graph sequencer did not finish");
    $finish;
  end
)sv";
  out << probe_footer();
  return out.str();
}

std::string sv_control_slot_engine() {
  std::ostringstream out;
  out << probe_header("control_slot_engine");
  out << R"sv(  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic [31:0] layer_i;
  logic [2:0] control_op_index_i;
  logic [3:0] control_kind_i;
  logic busy_o;
  logic done_o;
  logic control_valid_o;
  logic control_ready_i;
  logic control_commit_o;
  logic [31:0] layer_o;
  logic [2:0] control_op_index_o;
  logic [3:0] control_kind_o;
  logic [31:0] issued_control_ops_o;
  logic [1:0] cycle_phase_o;

  e1_h1_tinyllama_control_slot_engine u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .layer_i(layer_i),
    .control_op_index_i(control_op_index_i),
    .control_kind_i(control_kind_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .control_valid_o(control_valid_o),
    .control_ready_i(control_ready_i),
    .control_commit_o(control_commit_o),
    .layer_o(layer_o),
    .control_op_index_o(control_op_index_o),
    .control_kind_o(control_kind_o),
    .issued_control_ops_o(issued_control_ops_o),
    .cycle_phase_o(cycle_phase_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 4)
      0: return "issue_selected_control_slot";
      1: return "read_selected_control_metadata";
      2: return "execute_selected_control_slot";
      3: return "commit_selected_control_slot";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("control_slot_engine", "single_control_slot");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    layer_i = 32'd3;
    control_op_index_i = 3'd2;
    control_kind_i = 4'd3;
    control_ready_i = 1'b1;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 16 && !done_o; cycle++) begin
      e1_h1_full_dpi_cycle("control_slot_engine", cycle, phase_name(cycle));
      control_ready_i = 1'b1;
      tick();
    end
    expect_u32("issued_control_ops_o", 0, 1, issued_control_ops_o);
    expect_u32("layer_o", 0, 3, layer_o);
    expect_u32("control_op_index_o", 0, 2, {29'd0, control_op_index_o});
    expect_u32("control_kind_o", 0, 3, {28'd0, control_kind_o});
    if (!done_o) $fatal(1, "control slot engine did not finish");
    $finish;
  end
)sv";
  out << probe_footer();
  return out.str();
}

std::string sv_control_scheduler() {
  std::ostringstream out;
  out << probe_header("control_scheduler");
  out << R"sv(  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic busy_o;
  logic done_o;
  logic control_valid_o;
  logic control_ready_i;
  logic control_commit_o;
  logic [31:0] layer_o;
  logic [2:0] control_op_index_o;
  logic [3:0] layer_op_slot_o;
  logic [3:0] control_kind_o;
  logic [31:0] issued_control_ops_o;
  logic [1:0] cycle_phase_o;

  e1_h1_tinyllama_control_scheduler u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .control_valid_o(control_valid_o),
    .control_ready_i(control_ready_i),
    .control_commit_o(control_commit_o),
    .layer_o(layer_o),
    .control_op_index_o(control_op_index_o),
    .layer_op_slot_o(layer_op_slot_o),
    .control_kind_o(control_kind_o),
    .issued_control_ops_o(issued_control_ops_o),
    .cycle_phase_o(cycle_phase_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 4)
      0: return "issue_control_op";
      1: return "read_control_metadata";
      2: return "execute_control_op";
      3: return "commit_control_op";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("control_scheduler", "all_154_control_ops");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    control_ready_i = 1'b1;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 900 && !done_o; cycle++) begin
      e1_h1_full_dpi_cycle("control_scheduler", cycle, phase_name(cycle));
      tick();
    end
    expect_u32("issued_control_ops_o", 0, 154, issued_control_ops_o);
    if (!done_o) $fatal(1, "control scheduler did not finish");
    $finish;
  end
)sv";
  out << probe_footer();
  return out.str();
}

std::string sv_linear_scheduler() {
  std::ostringstream out;
  out << probe_header("linear_scheduler");
  out << R"sv(  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic busy_o;
  logic done_o;
  logic error_o;
  logic cmd_valid_o;
  logic cmd_ready_i;
  logic [31:0] cmd_input_addr_o;
  logic [31:0] cmd_weight_addr_o;
  logic [31:0] cmd_output_addr_o;
  logic [15:0] cmd_rows_o;
  logic [15:0] cmd_cols_o;
  logic [15:0] cmd_depth_o;
  logic array_done_i;
  logic array_error_i;
  logic [31:0] layer_o;
  logic [2:0] op_index_o;
  logic [8:0] input_tile_o;
  logic [8:0] output_tile_o;
  logic [31:0] issued_commands_o;
  logic [2:0] cycle_phase_o;

  e1_h1_tinyllama_linear_scheduler u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .error_o(error_o),
    .cmd_valid_o(cmd_valid_o),
    .cmd_ready_i(cmd_ready_i),
    .cmd_input_addr_o(cmd_input_addr_o),
    .cmd_weight_addr_o(cmd_weight_addr_o),
    .cmd_output_addr_o(cmd_output_addr_o),
    .cmd_rows_o(cmd_rows_o),
    .cmd_cols_o(cmd_cols_o),
    .cmd_depth_o(cmd_depth_o),
    .array_done_i(array_done_i),
    .array_error_i(array_error_i),
    .layer_o(layer_o),
    .op_index_o(op_index_o),
    .input_tile_o(input_tile_o),
    .output_tile_o(output_tile_o),
    .issued_commands_o(issued_commands_o),
    .cycle_phase_o(cycle_phase_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 8)
      0: return "setup_tile_command";
      1: return "assert_scheduler_valid";
      2: return "accept_command_handshake";
      3: return "wait_for_array_progress_0";
      4: return "wait_for_array_progress_1";
      5: return "wait_for_array_progress_2";
      6: return "sample_array_done";
      7: return "advance_tile_counters";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("linear_scheduler", "first_four_tile_commands");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    cmd_ready_i = 1'b1;
    array_done_i = 1'b0;
    array_error_i = 1'b0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 128 && issued_commands_o < 32'd4; cycle++) begin
      e1_h1_full_dpi_cycle("linear_scheduler", cycle, phase_name(cycle));
      array_done_i = (cycle_phase_o == 3'd6);
      tick();
      array_done_i = 1'b0;
    end
    expect_u32("issued_commands_o", 0, 4, issued_commands_o);
    expect_u32("cmd_rows_o", 0, 16, {16'd0, cmd_rows_o});
    expect_u32("cmd_cols_o", 0, 16, {16'd0, cmd_cols_o});
    expect_u32("cmd_depth_o", 0, 16, {16'd0, cmd_depth_o});
    if (error_o) $fatal(1, "linear scheduler reported error");
    $finish;
  end
)sv";
  out << probe_footer();
  return out.str();
}

std::string sv_linear_tile_engine() {
  std::ostringstream out;
  out << probe_header("linear_tile_engine");
  out << R"sv(  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic stream_valid_i;
  logic stream_ready_o;
  logic [63:0] stream_data_i;
  logic stream_last_i;
  logic stream_error_i;
  logic busy_o;
  logic done_o;
  logic error_o;
  logic [31:0] issued_commands_o;
  logic [2:0] cycle_phase_o;
  logic [31:0] layer_o;
  logic [2:0] op_index_o;
  logic [8:0] input_tile_o;
  logic [8:0] output_tile_o;
  logic scheduler_cmd_valid_o;
  logic array_cmd_valid_o;
  logic array_cmd_ready_o;
  logic [31:0] cmd_input_addr_o;
  logic [31:0] cmd_weight_addr_o;
  logic [31:0] cmd_output_addr_o;
  logic [15:0] cmd_rows_o;
  logic [15:0] cmd_cols_o;
  logic [15:0] cmd_depth_o;
  logic buffer_array_valid_o;
  logic buffer_array_ready_o;
  logic [63:0] buffer_array_data_o;
  logic array_done_o;
  logic array_debug_busy_o;
  logic [31:0] array_result_digest_o;

  e1_h1_tinyllama_linear_tile_engine u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .error_o(error_o),
    .issued_commands_o(issued_commands_o),
    .cycle_phase_o(cycle_phase_o),
    .layer_o(layer_o),
    .op_index_o(op_index_o),
    .input_tile_o(input_tile_o),
    .output_tile_o(output_tile_o),
    .scheduler_cmd_valid_o(scheduler_cmd_valid_o),
    .array_cmd_valid_o(array_cmd_valid_o),
    .array_cmd_ready_o(array_cmd_ready_o),
    .cmd_input_addr_o(cmd_input_addr_o),
    .cmd_weight_addr_o(cmd_weight_addr_o),
    .cmd_output_addr_o(cmd_output_addr_o),
    .cmd_rows_o(cmd_rows_o),
    .cmd_cols_o(cmd_cols_o),
    .cmd_depth_o(cmd_depth_o),
    .buffer_array_valid_o(buffer_array_valid_o),
    .buffer_array_ready_o(buffer_array_ready_o),
    .buffer_array_data_o(buffer_array_data_o),
    .array_done_o(array_done_o),
    .array_debug_busy_o(array_debug_busy_o),
    .array_result_digest_o(array_result_digest_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 8)
      0: return "setup_tile_engine";
      1: return "scheduler_valid_visible";
      2: return "array_command_handshake";
      3: return "latch_to_array_beat_0";
      4: return "latch_to_array_beat_1";
      5: return "latch_to_array_beat_2";
      6: return "array_done_pulse";
      7: return "return_ready";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("linear_tile_engine", "first_four_composed_tile_commands");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    stream_valid_i = 1'b0;
    stream_data_i = 64'd0;
    stream_last_i = 1'b0;
    stream_error_i = 1'b0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 256 && issued_commands_o < 32'd4; cycle++) begin
      e1_h1_full_dpi_cycle("linear_tile_engine", cycle, phase_name(cycle));
      stream_valid_i = 1'b1;
      stream_data_i = 64'h3000 + cycle[15:0];
      tick();
    end
    expect_u32("issued_commands_o", 0, 4, issued_commands_o);
    if (error_o) $fatal(1, "linear tile engine reported error");
    $finish;
  end
)sv";
  out << probe_footer();
  return out.str();
}

std::string sv_linear_slot_engine() {
  std::ostringstream out;
  out << probe_header("linear_slot_engine");
  out << R"sv(  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic [31:0] layer_i;
  logic [2:0] op_index_i;
  logic stream_valid_i;
  logic stream_ready_o;
  logic [63:0] stream_data_i;
  logic stream_last_i;
  logic stream_error_i;
  logic busy_o;
  logic done_o;
  logic error_o;
  logic [31:0] issued_commands_o;
  logic [31:0] expected_commands_o;
  logic [2:0] cycle_phase_o;
  logic [31:0] layer_o;
  logic [2:0] op_index_o;
  logic [8:0] input_tile_o;
  logic [8:0] output_tile_o;
  logic scheduler_cmd_valid_o;
  logic array_cmd_valid_o;
  logic array_cmd_ready_o;
  logic [31:0] cmd_input_addr_o;
  logic [31:0] cmd_weight_addr_o;
  logic [31:0] cmd_output_addr_o;
  logic [15:0] cmd_rows_o;
  logic [15:0] cmd_cols_o;
  logic [15:0] cmd_depth_o;
  logic buffer_array_valid_o;
  logic buffer_array_ready_o;
  logic [63:0] buffer_array_data_o;
  logic array_done_o;
  logic array_debug_busy_o;
  logic [31:0] array_result_digest_o;

  e1_h1_tinyllama_linear_slot_engine #(
    .SmokeMaxTilesPerLinearSlot(2)
  ) u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .layer_i(layer_i),
    .op_index_i(op_index_i),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .error_o(error_o),
    .issued_commands_o(issued_commands_o),
    .expected_commands_o(expected_commands_o),
    .cycle_phase_o(cycle_phase_o),
    .layer_o(layer_o),
    .op_index_o(op_index_o),
    .input_tile_o(input_tile_o),
    .output_tile_o(output_tile_o),
    .scheduler_cmd_valid_o(scheduler_cmd_valid_o),
    .array_cmd_valid_o(array_cmd_valid_o),
    .array_cmd_ready_o(array_cmd_ready_o),
    .cmd_input_addr_o(cmd_input_addr_o),
    .cmd_weight_addr_o(cmd_weight_addr_o),
    .cmd_output_addr_o(cmd_output_addr_o),
    .cmd_rows_o(cmd_rows_o),
    .cmd_cols_o(cmd_cols_o),
    .cmd_depth_o(cmd_depth_o),
    .buffer_array_valid_o(buffer_array_valid_o),
    .buffer_array_ready_o(buffer_array_ready_o),
    .buffer_array_data_o(buffer_array_data_o),
    .array_done_o(array_done_o),
    .array_debug_busy_o(array_debug_busy_o),
    .array_result_digest_o(array_result_digest_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 8)
      0: return "latch_selected_linear_slot";
      1: return "slot_command_valid";
      2: return "array_command_handshake";
      3: return "latch_to_array_beat_0";
      4: return "latch_to_array_beat_1";
      5: return "latch_to_array_beat_2";
      6: return "array_done_pulse";
      7: return "slot_done_or_next_tile";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    e1_h1_full_dpi_begin("linear_slot_engine", "bounded_two_tile_linear_slot");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    layer_i = 32'd0;
    op_index_i = 3'd0;
    stream_valid_i = 1'b0;
    stream_data_i = 64'd0;
    stream_last_i = 1'b0;
    stream_error_i = 1'b0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 128 && !done_o; cycle++) begin
      e1_h1_full_dpi_cycle("linear_slot_engine", cycle, phase_name(cycle));
      stream_valid_i = 1'b1;
      stream_data_i = 64'h4000 + cycle[15:0];
      tick();
    end
    expect_u32("issued_commands_o", 0, 2, issued_commands_o);
    expect_u32("expected_commands_o", 0, 2, expected_commands_o);
    if (!done_o) $fatal(1, "linear slot engine did not finish");
    if (error_o) $fatal(1, "linear slot engine reported error");
    $finish;
  end
)sv";
  out << probe_footer();
  return out.str();
}

std::string sv_full_top() {
  std::ostringstream out;
  out << probe_header("full_checkpoint_top");
  out << R"sv(  logic clk_i;
  logic rst_ni;
  logic start_i;
  logic stream_valid_i;
  logic stream_ready_o;
  logic [63:0] stream_data_i;
  logic stream_last_i;
  logic stream_error_i;
  logic busy_o;
  logic done_o;
  logic error_o;
  logic [31:0] issued_graph_slots_o;
  logic [31:0] issued_linear_commands_o;
  logic [31:0] issued_control_ops_o;
  logic [31:0] active_layer_o;
  logic [3:0] active_slot_o;
  logic [1:0] graph_cycle_phase_o;
  logic [2:0] linear_cycle_phase_o;
  logic [1:0] control_cycle_phase_o;
  logic launch_linear_o;
  logic launch_control_o;
  logic linear_busy_o;
  logic control_busy_o;
  logic buffer_array_valid_o;
  logic buffer_array_ready_o;
  logic array_done_o;
  logic array_debug_busy_o;
  logic [31:0] array_result_digest_o;
  logic debug_scheduler_cmd_valid_o;
  logic debug_array_cmd_valid_o;
  logic debug_array_cmd_ready_o;
  logic [31:0] debug_cmd_input_addr_o;
  logic [31:0] debug_cmd_weight_addr_o;
  logic [31:0] debug_cmd_output_addr_o;
  logic [15:0] debug_cmd_rows_o;
  logic [15:0] debug_cmd_cols_o;
  logic [15:0] debug_cmd_depth_o;
  logic [31:0] debug_linear_layer_o;
  logic [2:0] debug_linear_op_index_o;
  logic [8:0] debug_linear_input_tile_o;
  logic [8:0] debug_linear_output_tile_o;
  logic debug_control_valid_o;
  logic debug_control_commit_o;
  logic [31:0] debug_control_layer_o;
  logic [2:0] debug_control_op_index_o;
  logic [3:0] debug_control_kind_o;

  e1_h1_tinyllama_full_checkpoint_top #(
    .SmokeMaxTilesPerLinearSlot(1)
  ) u_dut (
    .clk_i(clk_i),
    .rst_ni(rst_ni),
    .start_i(start_i),
    .stream_valid_i(stream_valid_i),
    .stream_ready_o(stream_ready_o),
    .stream_data_i(stream_data_i),
    .stream_last_i(stream_last_i),
    .stream_error_i(stream_error_i),
    .busy_o(busy_o),
    .done_o(done_o),
    .error_o(error_o),
    .issued_graph_slots_o(issued_graph_slots_o),
    .issued_linear_commands_o(issued_linear_commands_o),
    .issued_control_ops_o(issued_control_ops_o),
    .active_layer_o(active_layer_o),
    .active_slot_o(active_slot_o),
    .graph_cycle_phase_o(graph_cycle_phase_o),
    .linear_cycle_phase_o(linear_cycle_phase_o),
    .control_cycle_phase_o(control_cycle_phase_o),
    .launch_linear_o(launch_linear_o),
    .launch_control_o(launch_control_o),
    .linear_busy_o(linear_busy_o),
    .control_busy_o(control_busy_o),
    .buffer_array_valid_o(buffer_array_valid_o),
    .buffer_array_ready_o(buffer_array_ready_o),
    .array_done_o(array_done_o),
    .array_debug_busy_o(array_debug_busy_o),
    .array_result_digest_o(array_result_digest_o),
    .debug_scheduler_cmd_valid_o(debug_scheduler_cmd_valid_o),
    .debug_array_cmd_valid_o(debug_array_cmd_valid_o),
    .debug_array_cmd_ready_o(debug_array_cmd_ready_o),
    .debug_cmd_input_addr_o(debug_cmd_input_addr_o),
    .debug_cmd_weight_addr_o(debug_cmd_weight_addr_o),
    .debug_cmd_output_addr_o(debug_cmd_output_addr_o),
    .debug_cmd_rows_o(debug_cmd_rows_o),
    .debug_cmd_cols_o(debug_cmd_cols_o),
    .debug_cmd_depth_o(debug_cmd_depth_o),
    .debug_linear_layer_o(debug_linear_layer_o),
    .debug_linear_op_index_o(debug_linear_op_index_o),
    .debug_linear_input_tile_o(debug_linear_input_tile_o),
    .debug_linear_output_tile_o(debug_linear_output_tile_o),
    .debug_control_valid_o(debug_control_valid_o),
    .debug_control_commit_o(debug_control_commit_o),
    .debug_control_layer_o(debug_control_layer_o),
    .debug_control_op_index_o(debug_control_op_index_o),
    .debug_control_kind_o(debug_control_kind_o)
  );

  function automatic string phase_name(input int cycle);
    case (cycle % 4)
      0: return "present_top_graph_slot";
      1: return "start_selected_slot_engine";
      2: return "run_selected_slot_engine";
      3: return "commit_top_graph_slot";
      default: return "invalid_cycle";
    endcase
  endfunction

  initial begin
    int linear_launches;
    int control_launches;
    e1_h1_full_dpi_begin("full_checkpoint_top", "bounded_all_308_graph_slots");
    clk_i = 1'b0;
    rst_ni = 1'b0;
    start_i = 1'b0;
    stream_valid_i = 1'b0;
    stream_data_i = 64'd0;
    stream_last_i = 1'b0;
    stream_error_i = 1'b0;
    linear_launches = 0;
    control_launches = 0;
    tick();
    tick();
    rst_ni = 1'b1;
    start_i = 1'b1;
    tick();
    start_i = 1'b0;
    for (int cycle = 0; cycle < 10000 && !done_o; cycle++) begin
      e1_h1_full_dpi_cycle("full_checkpoint_top", cycle, phase_name(cycle));
      stream_valid_i = 1'b1;
      stream_data_i = 64'h5000 + cycle[15:0];
      if (launch_linear_o) linear_launches++;
      if (launch_control_o) control_launches++;
      tick();
    end
    expect_u32("issued_graph_slots_o", 0, 308, issued_graph_slots_o);
    expect_u32("issued_linear_commands_o", 0, 154, issued_linear_commands_o);
    expect_u32("issued_control_ops_o", 0, 154, issued_control_ops_o);
    expect_u32("linear_launches", 0, 154, linear_launches[31:0]);
    expect_u32("control_launches", 0, 154, control_launches[31:0]);
    if (!done_o) $fatal(1, "full checkpoint top did not finish");
    if (error_o) $fatal(1, "full checkpoint top reported error");
    $finish;
  end
)sv";
  out << probe_footer();
  return out.str();
}

std::vector<ModuleSpec> module_specs() {
  const std::string gen = "e1/e1-h1/generated/full_checkpoint/";
  const std::string stream = "e1/e1-h1/rtl/imp2/e1_h1_stream_sram.sv";
  const std::string array = "e1/e1-h1/rtl/imp2/e1_h1_systolic_array.sv";
  std::vector<ModuleSpec> specs = {
      {
          "linear_scheduler",
          "e1_h1_tinyllama_linear_scheduler",
          "e1_h1_full_checkpoint_module_dpi_linear_scheduler",
          {gen + "e1_h1_tinyllama_linear_scheduler.sv"},
          {"cycle 0 setup", "cycle 2 command handshake", "cycle 6 array done"},
          {
              {"clk_i", "1", "Scheduler clock."},
              {"rst_ni", "1", "Active-low reset."},
              {"start_i", "1", "Starts full linear tile-command enumeration."},
              {"cmd_ready_i", "1", "Array-side command ready handshake."},
              {"array_done_i", "1", "Array completion for the issued tile command."},
              {"array_error_i", "1", "Array error for the issued tile command."},
          },
          {
              {"busy_o", "1", "Scheduler is running."},
              {"done_o", "1", "All full-checkpoint tile commands have been issued."},
              {"error_o", "1", "Scheduler observed array error."},
              {"cmd_valid_o", "1", "Tile command is valid."},
              {"cmd_input_addr_o", "32", "Input tile base address."},
              {"cmd_weight_addr_o", "32", "Weight tile base address."},
              {"cmd_output_addr_o", "32", "Output tile base address."},
              {"cmd_rows_o", "16", "Tile row count."},
              {"cmd_cols_o", "16", "Tile column count."},
              {"cmd_depth_o", "16", "Tile reduction depth."},
              {"layer_o", "32", "Current TinyLlama layer."},
              {"op_index_o", "3", "Current linear op index within the layer."},
              {"input_tile_o", "9", "Current input tile index."},
              {"output_tile_o", "9", "Current output tile index."},
              {"issued_commands_o", "32", "Number of accepted tile commands."},
              {"cycle_phase_o", "3", "Current 8-cycle tile-template phase."},
          },
          sv_linear_scheduler(),
      },
      {
          "linear_tile_engine",
          "e1_h1_tinyllama_linear_tile_engine",
          "e1_h1_full_checkpoint_module_dpi_linear_tile_engine",
          {gen + "e1_h1_tinyllama_linear_scheduler.sv", stream, array, gen + "e1_h1_tinyllama_linear_tile_engine.sv"},
          {"cycle 1 scheduler valid", "cycle 2 gated array command", "cycles 3-6 latch/array transfer"},
          {
              {"clk_i", "1", "Tile engine clock."},
              {"rst_ni", "1", "Active-low reset."},
              {"start_i", "1", "Starts the linear tile engine."},
              {"stream_valid_i", "1", "External input stream valid into the latch buffer."},
              {"stream_data_i", "64", "External input stream data into the latch buffer."},
              {"stream_last_i", "1", "External input stream packet-last marker."},
              {"stream_error_i", "1", "External input stream error marker."},
          },
          {
              {"stream_ready_o", "1", "Latch buffer can accept stream data."},
              {"busy_o", "1", "Scheduler is running."},
              {"done_o", "1", "Scheduler completed all tile commands."},
              {"error_o", "1", "Scheduler or array reported error."},
              {"issued_commands_o", "32", "Number of accepted tile commands."},
              {"cycle_phase_o", "3", "Current 8-cycle tile-template phase."},
              {"layer_o", "32", "Current TinyLlama layer."},
              {"op_index_o", "3", "Current linear op index."},
              {"input_tile_o", "9", "Current input tile index."},
              {"output_tile_o", "9", "Current output tile index."},
              {"scheduler_cmd_valid_o", "1", "Ungated scheduler command-valid signal."},
              {"array_cmd_valid_o", "1", "Gated command-valid signal sent to the array."},
              {"array_cmd_ready_o", "1", "Array command-ready signal."},
              {"cmd_input_addr_o", "32", "Input tile base address."},
              {"cmd_weight_addr_o", "32", "Weight tile base address."},
              {"cmd_output_addr_o", "32", "Output tile base address."},
              {"cmd_rows_o", "16", "Tile row count."},
              {"cmd_cols_o", "16", "Tile column count."},
              {"cmd_depth_o", "16", "Tile reduction depth."},
              {"buffer_array_valid_o", "1", "Latch-buffer output valid toward the array."},
              {"buffer_array_ready_o", "1", "Array input-ready signal toward the latch buffer."},
              {"buffer_array_data_o", "64", "Latched stream data toward the array."},
              {"array_done_o", "1", "Array completion pulse."},
              {"array_debug_busy_o", "1", "Array debug busy signal."},
              {"array_result_digest_o", "32", "Observed systolic-array result digest."},
          },
          sv_linear_tile_engine(),
      },
      {
          "control_scheduler",
          "e1_h1_tinyllama_control_scheduler",
          "e1_h1_full_checkpoint_module_dpi_control_scheduler",
          {gen + "e1_h1_tinyllama_control_scheduler.sv"},
          {"cycle 0 control issue", "cycle 2 execute", "cycle 3 commit"},
          {
              {"clk_i", "1", "Control scheduler clock."},
              {"rst_ni", "1", "Active-low reset."},
              {"start_i", "1", "Starts all-layer control-op scheduling."},
              {"control_ready_i", "1", "CPU/control side accepts the current control op."},
          },
          {
              {"busy_o", "1", "Control scheduler is running."},
              {"done_o", "1", "All control ops have been issued."},
              {"control_valid_o", "1", "Current control op is valid."},
              {"control_commit_o", "1", "Current control op commits this cycle."},
              {"layer_o", "32", "Current TinyLlama layer."},
              {"control_op_index_o", "3", "Control op index within the layer."},
              {"layer_op_slot_o", "4", "Original ordered graph slot for the control op."},
              {"control_kind_o", "4", "Encoded control operation kind."},
              {"issued_control_ops_o", "32", "Number of committed control ops."},
              {"cycle_phase_o", "2", "Current 4-cycle control template phase."},
          },
          sv_control_scheduler(),
      },
      {
          "graph_sequencer",
          "e1_h1_tinyllama_graph_sequencer",
          "e1_h1_full_checkpoint_module_dpi_graph_sequencer",
          {gen + "e1_h1_tinyllama_graph_sequencer.sv"},
          {"cycle 0 present graph slot", "cycle 1 launch selected engine", "cycle 2 wait done", "cycle 3 commit"},
          {
              {"clk_i", "1", "Graph sequencer clock."},
              {"rst_ni", "1", "Active-low reset."},
              {"start_i", "1", "Starts ordered TinyLlama graph-slot sequencing."},
              {"slot_ready_i", "1", "Downstream accepts the presented graph slot."},
              {"op_done_i", "1", "Selected slot engine has completed."},
          },
          {
              {"busy_o", "1", "Graph sequencer is running."},
              {"done_o", "1", "All graph slots have been issued."},
              {"slot_valid_o", "1", "Current ordered graph slot is valid."},
              {"launch_control_o", "1", "Launch pulse for the control slot engine."},
              {"launch_linear_o", "1", "Launch pulse for the linear slot engine."},
              {"layer_o", "32", "Current TinyLlama layer."},
              {"layer_slot_o", "4", "Ordered graph slot within the current layer."},
              {"linear_op_index_o", "3", "Linear op index for linear slots."},
              {"control_op_index_o", "3", "Control op index for control slots."},
              {"control_kind_o", "4", "Encoded control operation kind."},
              {"linear_tile_count_o", "32", "Planned tile count for linear slots."},
              {"issued_graph_slots_o", "32", "Number of committed graph slots."},
              {"cycle_phase_o", "2", "Current 4-cycle graph template phase."},
          },
          sv_graph_sequencer(),
      },
      {
          "linear_slot_engine",
          "e1_h1_tinyllama_linear_slot_engine",
          "e1_h1_full_checkpoint_module_dpi_linear_slot_engine",
          {stream, array, gen + "e1_h1_tinyllama_linear_slot_engine.sv"},
          {"cycle 1 slot command valid", "cycle 2 array command", "cycles 3-6 separated latch/array"},
          {
              {"clk_i", "1", "Linear slot engine clock."},
              {"rst_ni", "1", "Active-low reset."},
              {"start_i", "1", "Starts one selected linear graph slot."},
              {"layer_i", "32", "Layer selected by the graph sequencer."},
              {"op_index_i", "3", "Linear op index selected by the graph sequencer."},
              {"stream_valid_i", "1", "External input stream valid into the latch buffer."},
              {"stream_data_i", "64", "External input stream data into the latch buffer."},
              {"stream_last_i", "1", "External input stream packet-last marker."},
              {"stream_error_i", "1", "External input stream error marker."},
          },
          {
              {"stream_ready_o", "1", "Latch buffer can accept stream data."},
              {"busy_o", "1", "Linear slot engine is running."},
              {"done_o", "1", "Selected linear slot is complete."},
              {"error_o", "1", "Array reported error for the selected slot."},
              {"issued_commands_o", "32", "Number of accepted tile commands for this slot."},
              {"expected_commands_o", "32", "Bounded or natural tile count for this slot."},
              {"cycle_phase_o", "3", "Current 8-cycle tile-template phase."},
              {"layer_o", "32", "Latched layer for this slot."},
              {"op_index_o", "3", "Latched linear op index for this slot."},
              {"input_tile_o", "9", "Current input tile index."},
              {"output_tile_o", "9", "Current output tile index."},
              {"scheduler_cmd_valid_o", "1", "Ungated slot command-valid signal."},
              {"array_cmd_valid_o", "1", "Gated command-valid signal sent to the array."},
              {"array_cmd_ready_o", "1", "Array command-ready signal."},
              {"cmd_input_addr_o", "32", "Input tile base address."},
              {"cmd_weight_addr_o", "32", "Weight tile base address."},
              {"cmd_output_addr_o", "32", "Output tile base address."},
              {"cmd_rows_o", "16", "Tile row count."},
              {"cmd_cols_o", "16", "Tile column count."},
              {"cmd_depth_o", "16", "Tile reduction depth."},
              {"buffer_array_valid_o", "1", "Latch-buffer output valid toward the array."},
              {"buffer_array_ready_o", "1", "Array input-ready signal toward the latch buffer."},
              {"buffer_array_data_o", "64", "Latched stream data toward the array."},
              {"array_done_o", "1", "Array completion pulse."},
              {"array_debug_busy_o", "1", "Array debug busy signal."},
              {"array_result_digest_o", "32", "Observed systolic-array result digest."},
          },
          sv_linear_slot_engine(),
      },
      {
          "control_slot_engine",
          "e1_h1_tinyllama_control_slot_engine",
          "e1_h1_full_checkpoint_module_dpi_control_slot_engine",
          {gen + "e1_h1_tinyllama_control_slot_engine.sv"},
          {"cycle 0 control valid", "cycle 2 execute", "cycle 3 commit"},
          {
              {"clk_i", "1", "Control slot engine clock."},
              {"rst_ni", "1", "Active-low reset."},
              {"start_i", "1", "Starts one selected control graph slot."},
              {"layer_i", "32", "Layer selected by the graph sequencer."},
              {"control_op_index_i", "3", "Control op index selected by the graph sequencer."},
              {"control_kind_i", "4", "Encoded control operation kind."},
              {"control_ready_i", "1", "CPU/control side accepts the selected control slot."},
          },
          {
              {"busy_o", "1", "Control slot engine is running."},
              {"done_o", "1", "Selected control slot is complete."},
              {"control_valid_o", "1", "Selected control slot is valid."},
              {"control_commit_o", "1", "Selected control slot commits this cycle."},
              {"layer_o", "32", "Latched layer for this slot."},
              {"control_op_index_o", "3", "Latched control op index for this slot."},
              {"control_kind_o", "4", "Latched control operation kind."},
              {"issued_control_ops_o", "32", "Number of committed control ops for this slot."},
              {"cycle_phase_o", "2", "Current 4-cycle control template phase."},
          },
          sv_control_slot_engine(),
      },
      {
          "full_checkpoint_top",
          "e1_h1_tinyllama_full_checkpoint_top",
          "e1_h1_full_checkpoint_module_dpi_full_checkpoint_top",
          {
              gen + "e1_h1_tinyllama_graph_sequencer.sv",
              stream,
              array,
              gen + "e1_h1_tinyllama_linear_slot_engine.sv",
              gen + "e1_h1_tinyllama_control_slot_engine.sv",
              gen + "e1_h1_tinyllama_full_checkpoint_top.sv",
          },
          {"cycle 0 graph slot", "cycle 1 slot start", "cycle 2 selected engine runs", "cycle 3 graph commit"},
          {
              {"clk_i", "1", "Full-checkpoint top clock."},
              {"rst_ni", "1", "Active-low reset."},
              {"start_i", "1", "Starts ordered full-checkpoint graph-slot execution."},
              {"stream_valid_i", "1", "External input stream valid into linear slots."},
              {"stream_data_i", "64", "External input stream data into linear slots."},
              {"stream_last_i", "1", "External input stream packet-last marker."},
              {"stream_error_i", "1", "External input stream error marker."},
          },
          {
              {"stream_ready_o", "1", "Selected linear slot can accept stream data."},
              {"busy_o", "1", "Full-checkpoint top is running."},
              {"done_o", "1", "All graph slots have completed."},
              {"error_o", "1", "Selected linear slot reported error."},
              {"issued_graph_slots_o", "32", "Number of committed graph slots."},
              {"issued_linear_commands_o", "32", "Number of accepted bounded linear tile commands."},
              {"issued_control_ops_o", "32", "Number of committed control slots."},
              {"active_layer_o", "32", "Current TinyLlama layer."},
              {"active_slot_o", "4", "Current ordered graph slot within the layer."},
              {"graph_cycle_phase_o", "2", "Current 4-cycle graph template phase."},
              {"linear_cycle_phase_o", "3", "Current linear slot tile-template phase."},
              {"control_cycle_phase_o", "2", "Current control slot template phase."},
              {"launch_linear_o", "1", "Launch pulse for the linear slot engine."},
              {"launch_control_o", "1", "Launch pulse for the control slot engine."},
              {"linear_busy_o", "1", "Linear slot engine is running."},
              {"control_busy_o", "1", "Control slot engine is running."},
              {"buffer_array_valid_o", "1", "Latch-buffer output valid toward the array."},
              {"buffer_array_ready_o", "1", "Array input-ready signal toward the latch buffer."},
              {"array_done_o", "1", "Array completion pulse."},
              {"array_debug_busy_o", "1", "Array debug busy signal."},
              {"array_result_digest_o", "32", "Observed systolic-array result digest."},
              {"debug_scheduler_cmd_valid_o", "1", "Observed scheduler command-valid at the full top boundary."},
              {"debug_array_cmd_valid_o", "1", "Observed array command-valid at the full top boundary."},
              {"debug_array_cmd_ready_o", "1", "Observed array command-ready at the full top boundary."},
              {"debug_cmd_input_addr_o", "32", "Observed input tile base address for payload schedule checks."},
              {"debug_cmd_weight_addr_o", "32", "Observed weight tile base address for payload schedule checks."},
              {"debug_cmd_output_addr_o", "32", "Observed output tile base address for payload schedule checks."},
              {"debug_cmd_rows_o", "16", "Observed tile row count for payload schedule checks."},
              {"debug_cmd_cols_o", "16", "Observed tile column count for payload schedule checks."},
              {"debug_cmd_depth_o", "16", "Observed tile reduction depth for payload schedule checks."},
              {"debug_linear_layer_o", "32", "Observed linear slot layer for payload schedule checks."},
              {"debug_linear_op_index_o", "3", "Observed linear op index for payload schedule checks."},
              {"debug_linear_input_tile_o", "9", "Observed input tile index for payload schedule checks."},
              {"debug_linear_output_tile_o", "9", "Observed output tile index for payload schedule checks."},
              {"debug_control_valid_o", "1", "Observed CPU/control slot payload-valid at the full top boundary."},
              {"debug_control_commit_o", "1", "Observed CPU/control slot commit at the full top boundary."},
              {"debug_control_layer_o", "32", "Observed CPU/control slot layer for graph schedule checks."},
              {"debug_control_op_index_o", "3", "Observed CPU/control op index for graph schedule checks."},
              {"debug_control_kind_o", "4", "Observed CPU/control op kind for graph schedule checks."},
          },
          sv_full_top(),
      },
  };
  for (ModuleSpec& spec : specs) {
    spec.probe_sv = probe_sv_with_phase_signal_checks(spec);
  }
  return specs;
}

fs::path parse_path_arg(int argc, char** argv, const std::string& flag, const fs::path& default_value) {
  for (int i = 1; i + 1 < argc; ++i) {
    if (argv[i] == flag) {
      return fs::path(argv[i + 1]);
    }
  }
  return default_value;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const fs::path repo_root = parse_path_arg(argc, argv, "--repo-root", fs::current_path());
    const fs::path output_dir = parse_path_arg(
        argc,
        argv,
        "--output-dir",
        repo_root / "e1/e1-h1/generated/full_checkpoint_dpi");
    const fs::path rel_output_dir = fs::relative(output_dir, repo_root);
    const fs::path flist_dir = output_dir / "flists";
    const std::vector<ModuleSpec> specs = module_specs();

    for (const ModuleSpec& spec : specs) {
      validate_rtl_inputs(repo_root, spec);
      validate_signal_contract(repo_root, spec);
      validate_isolation(repo_root, spec);
      validate_cycle_contract(spec);
    }
    validate_readme_cycle_coverage(repo_root, specs);

    write_text(output_dir / "e1_h1_full_checkpoint_module_dpi_scoreboard.cpp", scoreboard_cpp());
    for (const ModuleSpec& spec : specs) {
      const fs::path probe_path = rel_output_dir / (spec.probe_module + ".sv");
      write_text(output_dir / (spec.probe_module + ".sv"), spec.probe_sv);
      write_text(output_dir / (spec.probe_module + "_main.cpp"), main_cpp(spec));
      write_text(flist_dir / (spec.name + ".f"), flist_text(probe_path, spec));
    }
    write_text(output_dir / "manifest.json", manifest_json(specs));
    write_text(output_dir / "module_interfaces.md", module_interfaces_markdown(specs));
    write_text(output_dir / "module_isolation.json", module_isolation_json(specs));
    write_text(output_dir / "cycle_contract.json", cycle_contract_json(specs));
    write_text(output_dir / "module_test_plan.json", module_test_plan_json(specs));
    write_text(output_dir / "verilator_execution_recipe.json", verilator_execution_recipe_json(specs));
    write_text(output_dir / "e1_h1_full_checkpoint_module_dpi_verilator_launcher.cpp",
               verilator_launcher_cpp(specs));
    write_text(output_dir / "readme_cycle_coverage.json", readme_cycle_coverage_json(specs));
    write_text(output_dir / "construction_ledger.json", construction_ledger_json(specs));

    std::cout << "PASS e1_h1_generate_full_checkpoint_module_dpi " << specs.size()
              << " modules -> " << output_dir.generic_string() << "\n";
    return 0;
  } catch (const std::exception& ex) {
    std::cerr << "FAIL e1_h1_generate_full_checkpoint_module_dpi " << ex.what() << "\n";
    return 1;
  }
}
