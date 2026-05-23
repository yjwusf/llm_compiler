// SPDX-License-Identifier: Apache-2.0
#include <cstdint>

namespace {

const char* g_current_ip = "";
const char* g_current_case = "";

}  // namespace

extern "C" void e1_h1_dpi_begin(const char* ip_name, const char* vip_case) {
  g_current_ip = ip_name;
  g_current_case = vip_case;
}

extern "C" int e1_h1_dpi_compare_i(
    const char* signal_name,
    int cycle,
    int imp1_value,
    int imp2_value) {
  (void)signal_name;
  (void)cycle;
  (void)g_current_ip;
  (void)g_current_case;
  return imp1_value == imp2_value ? 1 : 0;
}
