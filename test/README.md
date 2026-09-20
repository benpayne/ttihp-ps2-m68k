# Testbench

A [cocotb](https://docs.cocotb.org/en/stable/) testbench for the PS/2 keyboard
decoder. The same 36 tests run against the RTL and, in CI, against the
post-layout gate-level netlist.

## Running

RTL simulation:

```sh
make -B
```

Gate-level simulation. First harden the project, copy
`../runs/wokwi/results/final/verilog/gl/tt_um_benpayne_ps2_decoder.v` to
`gate_level_netlist.v`, then:

```sh
make -B GATES=yes
```

Results land in `results.xml`. `make` exits successfully even when a test fails,
so check that file (CI does this with `! grep failure results.xml`).

## What's covered

| Group | Tests |
| --- | --- |
| Reset | Output values out of reset, reset mid-frame, reset while `cs` is held high |
| Basic decode | Single and repeated bytes, `0x00`/`0xFF`, PS/2 clocks from slow to the 16.7 kHz spec maximum, asymmetric duty cycle, partial frames |
| Frame errors | Parity, start-bit and stop-bit violations are rejected |
| Debounce | Glitches on the PS/2 clock and data lines are filtered out |
| Host interface | `cs` held high pops exactly one byte, `cs` glitches are rejected, reading an empty FIFO is harmless |
| Interrupt semantics | Interrupt re-asserts on new arrivals, and behaviour with bytes still queued |
| FIFO | Overflow, pointer wraparound, same-cycle read/write, back-to-back bytes, and a randomized soak |
| UART debug | Status and data bytes on `uo[4]`, including the `fifo_full` status encoding |
| Bring-up pins | `uo[5]`/`uo[6]` track the debouncer outputs rather than the raw pads, the clock tap sees all 11 bit-clocks of a frame, and `uo[7]` strobes exactly once per read |

Two things keep the suite honest beyond the individual assertions:

- **`invariant_monitor`** runs as a background task in every test and checks
  properties on *every* clock edge — that `uio_oe` tracks `cs`, that `valid` is
  never high on two consecutive cycles, that `interupt` only rises after a
  `valid`, and that `fifo_full` and empty are never asserted together.
- **`settle_offgrid`** places stimulus 5 ns after a clock edge rather than on
  one. Every delay in the suite is a multiple of the 40 ns clock period, so
  driving an input straight after a `Timer` would change it in the same
  simulated instant as the edge that samples it. RTL simulation happens to
  resolve that race favourably; gate-level simulation does not.

Every test but `test_reset_values` (which exercises reset itself) starts from
`reset_dut()`, so a failure doesn't cascade into the tests that follow it.

## Waveforms

The run writes `tb.fst`:

```sh
gtkwave tb.fst tb.gtkw   # or: surfer tb.fst
```

For VCD instead, change `$dumpfile` in [tb.v](tb.v) and run `make -B FST=`.

## Note on gate-level simulation

The vendor `sg13g2_stdcell.v` models sequential cells through specify-block
`delayed_*` nets that Icarus Verilog doesn't support, which makes every register
read X. [`strip_gl_timing_cells.py`](strip_gl_timing_cells.py) filters those cell
definitions out of a copy of the vendor file and
[`gl_sim_cell_models.v`](gl_sim_cell_models.v) substitutes functional-only
replacements. This affects simulation only — the real PDK files used for
synthesis and GDS are untouched.
