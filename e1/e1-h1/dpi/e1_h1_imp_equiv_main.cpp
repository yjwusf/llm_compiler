// SPDX-License-Identifier: Apache-2.0
#include "Ve1_h1_imp_equiv_probe.h"
#include "verilated.h"

int main(int argc, char** argv) {
  VerilatedContext context;
  context.commandArgs(argc, argv);
  Ve1_h1_imp_equiv_probe top{&context};
  while (!context.gotFinish() && context.time() < 1000) {
    top.eval();
    context.timeInc(1);
  }
  return 0;
}
