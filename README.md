![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# PS/2 Keyboard Decoder for 68k Systems (Tiny Tapeout IHP 26b)

A PS/2 keyboard decoder for interfacing retro 68k-based systems, targeting the
Tiny Tapeout IHP 26b shuttle (IHP SG13G2, 130nm SiGe BiCMOS).

The design debounces and synchronizes the PS/2 clock and data lines, decodes the
11-bit PS/2 frame (start + 8 data + parity + stop), validates it, and queues the
scan code in a 4-byte FIFO for the host to read over an 8-bit bus. A sticky
interrupt flag and a `data_rdy` status line let the host service bytes either by
interrupt or by polling. For bench debugging, every captured byte is also echoed
out a 115200 baud 8N1 UART on `uo[4]` as a status byte followed by the scan code.

This is a port of [ttgf-ps2-m68k](https://github.com/benpayne/ttgf-ps2-m68k),
originally built for the Tiny Tapeout GF0.2µm shuttle (GlobalFoundries GF180MCU).

> **External hardware required:** unlike GF180, IHP SG13G2 does not offer native
> 5V-tolerant I/O, so the 5V PS/2 CLK and DATA lines need external level shifting
> (or a resistive divider) before reaching `ui[0]` and `ui[1]`.

**[Read the full documentation, including host bus timing →](docs/info.md)**

## Pinout

| Pin | Name | Description |
| --- | --- | --- |
| `ui[0]` | `ps2_clk` | PS/2 clock (level-shifted) |
| `ui[1]` | `ps2_data` | PS/2 data (level-shifted) |
| `ui[2]` | `clear_int` | Clear the interrupt flag (active high, ≥2 clocks) |
| `ui[3]` | `cs` | Chip select / read strobe (active high, ≥2 clocks) |
| `uo[0]` | `valid` | Single-cycle pulse when a byte is decoded |
| `uo[1]` | `interupt` | Sticky interrupt flag, **active high** |
| `uo[2]` | `data_rdy` | FIFO has data to read |
| `uo[3]` | `fifo_full` | FIFO full; further bytes are dropped |
| `uo[4]` | `uart_tx` | 115200 8N1 debug output |
| `uo[5]` | `ps2_clk_dbg` | Debounced PS/2 clock (bring-up tap) |
| `uo[6]` | `ps2_data_dbg` | Debounced PS/2 data (bring-up tap) |
| `uo[7]` | `cs_trigger_dbg` | Internal FIFO read strobe (bring-up tap) |
| `uio[7:0]` | `data_out` | Scan code byte; driven while `cs` is high |

The system clock is 25 MHz. Allow at least 200 ns from `cs` rising before
sampling the data bus — see [docs/info.md](docs/info.md) for the full timing
rules, the interrupt semantics a driver needs to get right, and the fault
isolation table for bench bring-up.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/` | Verilog sources and the LibreLane hardening config |
| `test/` | cocotb testbench — see [test/README.md](test/README.md) |
| `docs/info.md` | Design documentation and datasheet text |
| `info.yaml` | Tiny Tapeout project metadata and pinout |

## Building

The GitHub Actions in this repo build the GDS with
[LibreLane](https://www.zerotoasiccourse.com/terminology/librelane/), run the
Tiny Tapeout precheck, and run the testbench against the post-layout netlist.
To harden locally instead, see the
[local hardening guide](https://www.tinytapeout.com/guides/local-hardening/).

## About Tiny Tapeout

Tiny Tapeout is an educational project that makes it easier and cheaper than
ever to get your digital and analog designs manufactured on a real chip. Learn
more at [tinytapeout.com](https://tinytapeout.com), or read the
[FAQ](https://tinytapeout.com/faq/).

## License

Apache-2.0 — see [LICENSE](LICENSE).
