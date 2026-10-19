# RTL prototype: descending-neuron stage

`gf_descending.v` is the Giant Fiber / non-GF descending pathway stage of the model (leaky
integrators with FlyWire synapse-count weights, GF spike, long/short escape-mode state machine)
in Q16.16 fixed point, one evaluation step per `step` pulse (1 ms by default).

What it is: a simulation-level Verilog model, checked against the Rust engine with Icarus Verilog.
What it is not: synthesised, timing-closed, or run on an FPGA/ASIC. The front end (event time
surface, looming estimator, LPLC2/LC4 activation functions) exists only in Rust.

Check (requires `iverilog`/`vvp` and the built native library):

    python3 rtl/check_rtl.py --json rtl/rtl_check.json

The script verifies that the weight/threshold parameters match `data.rs` and the default config.
It then feeds the LPLC2/LC4 activities from 11 Rust engine runs (looms with r/v of 5-80 ms, two
seeds each, plus a noise-only control) into the RTL and compares the outputs step by step. Last
result: see `rtl_check.json`. 10 of the 11 runs gave identical escape-mode sequences. In the
r/v = 20 ms, seed 0 run, the RTL entered the escape one step (1 ms) later than float32 Rust, because
the membrane sat within fixed-point rounding of the threshold. The maximum membrane difference was
about 3e-4 (normalised units).
