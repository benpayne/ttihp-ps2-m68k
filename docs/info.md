## How it works

This decoder works by first debouncing the inputs to make sure that we get a clean sample of them that is synchronized to our clock.  It then looks at the down transition of ps2_clk and reads the value of ps2_data.  It shifts this into a 11 bit shift register.  When ps2_clk remains high for more than 1/2 of the 10kHz ps2_clk cycle it knows that the end of the data has arrived.  It then triggers a valid flag to tell the system that something has arrived.  The valid flag, which is exposed on a pin, will trigger the fifo to read the byte of data and it will be stored for retrieval by the host.  When valid is triggered it will also trigger the interrupt pin.  The valid pin is a pulse for one system clock cycle, but the interrupt will remain set until it is cleared.  We also include a data_rdy signal that tells the host that there is data to read.  This is useful if your interrupt handler needs to read multiple bytes.

When the host wants to read a byte, it asserts the chip select (cs) signal when the system clock goes high.  This will result in the uio bus being set with the data value.  The uio bus will be put into an output state only when cs is asserted, at all other times it will be an input bus (but we never read it...).  The CS signal includes glitch filtering - it must be held stable high for at least 2 clock cycles (80ns @ 25MHz) to trigger a read.  This prevents accidental double-reads from noisy bus signals.

The design includes a fifo_full output signal that indicates when the FIFO buffer is full (4 bytes).  When full, additional bytes from the keyboard will be silently dropped until space becomes available.  Software should monitor this flag to detect potential data loss during rapid typing.

For debugging on the bench, uo[4] also carries a 115200 baud UART transmission of a status byte followed by the decoded PS/2 byte, sent each time a valid byte is captured.

## Port from TTGF0p2 (GF180) to TT IHP 26b (IHP SG13G2)

This project was originally built for the Tiny Tapeout GF0.2µm shuttle (GlobalFoundries GF180MCU, 180nm), which offered native 5V I/O tolerance and a 3.3V core. It has been ported here to the Tiny Tapeout IHP 26b shuttle, which targets the open source IHP SG13G2 (130nm SiGe BiCMOS) PDK.

**What changed:**

- **No VPWR/VGND ports** - the IHP template's top module interface (`ui_in, uo_out, uio_in, uio_out, uio_oe, ena, clk, rst_n`) doesn't require explicit power ports on every submodule, unlike the GF180 flow. All power routing is handled by the standard cell fabric.
- **I/O voltage** - IHP's standard digital pads are **not natively 5V-tolerant** like GF180's. PS/2 signaling is 5V, so this design now requires **external level shifting or a resistive voltage divider** on `ui_in[0]` (ps2_clk) and `ui_in[1]` (ps2_data) to bring the signal down to the IHP pad's supported input range before it reaches the chip. This is the same external hardware requirement the original Sky130-based TT08 version of this project needed - GF180's 5V tolerance was a temporary advantage that doesn't carry over to IHP.
- **Core logic unchanged** - the PS/2 protocol decoder, debouncer, and FIFO are bit-for-bit the same design that passed all 16 functional tests on the GF180 target; only the power/pad interface differs.

## How to test

Level-shift (or resistor-divide) a standard PS/2 keyboard's 5V CLK/DATA lines down to a voltage compatible with the IHP SG13G2 I/O pads, then connect them to ui_in[0] (ps2_clk) and ui_in[1] (ps2_data). At this point you can hit keys and they will be queued in the FIFO. Then interface a retro computer to the CS, interrupt and data lines to read the FIFO. This will depend on the system you're using, but note you'll need external address decoding logic and for chips like the 68000 you'll need to generate DTACK and other bus-timing signals elsewhere.

## External hardware

Connect a standard PS/2 keyboard to the chip through a level shifter / voltage divider:
- PS/2 pin 1 (DATA) → level shifter → ui_in[1]
- PS/2 pin 5 (CLK) → level shifter → ui_in[0]
- PS/2 pin 3 (GND) → GND
- PS/2 pin 4 (VCC) → 5V supply (keyboard side only)

**Note**: Unlike the GF180 version of this project, IHP SG13G2 does not offer native 5V-tolerant I/O, so level shifting (or a simple resistive divider) is required between the keyboard and ui_in[0:1].

Interface to your microprocessor:
- Connect CS, interrupt, and data_out[7:0] signals
- Add external address decoding logic as needed
- For 68000: Generate DTACK externally based on CS timing
