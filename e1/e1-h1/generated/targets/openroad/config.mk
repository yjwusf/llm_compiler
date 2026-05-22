# E1-H1 OpenROAD smoke package.
DESIGN_NAME := e1_h1_soc_top
VERILOG_FILES := $(shell cat rtl.filelist)
SDC_FILE := constraints.sdc
PLATFORM ?= sky130hd
