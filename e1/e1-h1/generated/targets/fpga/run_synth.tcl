# Smoke synthesis script fragment for E1-H1.
set top e1_h1_soc_top
set rtl_files [split [read [open rtl.filelist r]] "\n"]
foreach f $rtl_files { if {$f ne ""} { read_verilog -sv $f } }
synth_design -top $top
