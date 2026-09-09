# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import First, RisingEdge, Timer

CLK_NS = 40                 # 25 MHz system clock
PS2_HALF_US = 50            # default PS/2 half-period (10 kHz)
FIFO_DEPTH = 4
UART_BIT_NS = 217 * CLK_NS  # 115200 baud @ 25 MHz


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

async def settle_offgrid(dut):
    """Land 5ns after a clock edge before driving an input the DUT samples.

    Every delay in this suite is a clean multiple of the 40ns clock period, so
    an input written straight after a Timer would change on the *exact same
    simulated instant* as a clock edge - a same-timestep race between that
    write and the flip-flop sampling it, which RTL sim happens to resolve
    favorably and gate-level sim (more logic between the clock and the
    observable value) can lose.
    """
    await RisingEdge(dut.clk)
    await Timer(5, unit="ns")


async def reset_dut(dut):
    """Bring the DUT to a known state: clock running, reset applied, PS/2 idle,
    debouncers settled. Every test starts here so failures don't cascade."""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    dut.clear_int.value = 0
    dut.cs.value = 0
    dut.ps2_clk.value = 1
    dut.ps2_data.value = 1
    dut.rst_n.value = 0
    await Timer(1, unit="us")
    await settle_offgrid(dut)
    dut.rst_n.value = 1
    # 128-cycle debouncers need ~5us to see the idle-high PS/2 lines
    await Timer(10, unit="us")
    assert dut.interupt.value == 0, "interrupt not clear after reset"
    assert dut.data_rdy.value == 0, "FIFO not empty after reset"


def check_idle_outputs(dut, where):
    assert dut.valid.value == 0, f"{where}: valid should be 0"
    assert dut.interupt.value == 0, f"{where}: interupt should be 0"
    assert dut.data_rdy.value == 0, f"{where}: data_rdy should be 0"
    assert dut.fifo_full.value == 0, f"{where}: fifo_full should be 0"
    assert dut.uart_tx.value == 1, f"{where}: uart_tx should idle high"
    assert dut.uio_oe.value == 0x00, f"{where}: uio_oe should be 0 with cs low"
    assert int(dut.uio_out.value) == 0x00, f"{where}: uio_out should be 0"
    assert (int(dut.uo_out.value) >> 5) == 0, f"{where}: uo_out[7:5] should be 0"


# ---------------------------------------------------------------------------
# PS/2 keyboard model
# ---------------------------------------------------------------------------

def frame_bits(value, bit_count=8, parity_valid=True, stop_valid=True, start_bit=0):
    bits = [start_bit]
    parity = 0
    for i in range(bit_count):
        bit = (value >> i) & 1
        parity ^= bit
        bits.append(bit)
    bits.append((parity ^ 1) if parity_valid else parity)  # odd parity
    bits.append(1 if stop_valid else 0)
    return bits


async def send_bit(ps2_clk, ps2_data, bit, half_us=PS2_HALF_US):
    ps2_data.value = bit
    ps2_clk.value = 1
    await Timer(half_us, unit="us")
    ps2_clk.value = 0
    await Timer(half_us, unit="us")


async def send_bits(ps2_clk, ps2_data, value, bit_count=8, parity_valid=True,
                    stop_valid=True, half_us=PS2_HALF_US):
    for bit in frame_bits(value, bit_count, parity_valid, stop_valid):
        await send_bit(ps2_clk, ps2_data, bit, half_us)
    ps2_clk.value = 1


async def send_two_bytes(ps2_clk, ps2_data, value1, value2, gap_us=100):
    await send_bits(ps2_clk, ps2_data, value1)
    await Timer(gap_us, unit="us")
    await send_bits(ps2_clk, ps2_data, value2)


async def send_and_wait_valid(dut, value, **kw):
    cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, value, **kw))
    await RisingEdge(dut.valid)
    await Timer(80, unit="ns")


async def expect_no_valid(dut, window_ms):
    res = await First(RisingEdge(dut.valid), Timer(window_ms, unit="ms"))
    assert isinstance(res, Timer), "unexpected valid pulse"


# ---------------------------------------------------------------------------
# Host model
# ---------------------------------------------------------------------------

async def read_byte(dut):
    """One host read: raise cs, wait for the glitch filter + FIFO read
    register, sample the bus, drop cs."""
    await settle_offgrid(dut)
    assert dut.uio_oe.value == 0x00, "uio_oe must not be set before a read"
    dut.cs.value = 1
    # data_out is registered on the 3rd clock edge after cs rises (2-cycle
    # glitch filter, then the FIFO's own read register); we sample after the 4th.
    await Timer(160, unit="ns")
    assert dut.uio_oe.value == 0xFF, "uio_oe must be set when reading data"
    value = int(dut.uio_out.value)
    dut.cs.value = 0
    return value


async def pulse_clear_int(dut):
    await settle_offgrid(dut)
    dut.clear_int.value = 1
    await Timer(80, unit="ns")
    dut.clear_int.value = 0
    await Timer(40, unit="ns")


# ---------------------------------------------------------------------------
# UART receiver model (uo[4], 115200 8N1)
# ---------------------------------------------------------------------------

async def uart_receive_byte(dut, timeout_us):
    start = cocotb.utils.get_sim_time(unit="us")
    while dut.uart_tx.value == 1:
        await Timer(100, unit="ns")
        assert cocotb.utils.get_sim_time(unit="us") - start < timeout_us, \
            "UART start bit timeout"
    await Timer(UART_BIT_NS // 2, unit="ns")
    assert dut.uart_tx.value == 0, "UART start bit must be 0"
    data = 0
    for i in range(8):
        await Timer(UART_BIT_NS, unit="ns")
        data |= int(dut.uart_tx.value) << i
    await Timer(UART_BIT_NS, unit="ns")
    assert dut.uart_tx.value == 1, "UART stop bit must be 1"
    return data


async def uart_receive_frame(dut, timeout_us=2000):
    """The DUT sends a status byte followed by the PS/2 data byte."""
    status = await uart_receive_byte(dut, timeout_us)
    data = await uart_receive_byte(dut, timeout_us=500)
    return status, data


def uart_status(fifo_full, data_rdy, interupt):
    return (fifo_full << 3) | (data_rdy << 2) | (interupt << 1) | 1


# ===========================================================================
# Reset
# ===========================================================================

@cocotb.test()
async def test_reset_values(dut):
    """All outputs are defined and idle from the moment reset is applied -
    before the first clock edge - and stay that way afterwards."""
    dut.clear_int.value = 0
    dut.cs.value = 0
    dut.ps2_clk.value = 1
    dut.ps2_data.value = 1
    dut.rst_n.value = 0
    await Timer(1, unit="us")
    check_idle_outputs(dut, "in reset, no clock")

    dut.rst_n.value = 1
    await Timer(1, unit="us")
    check_idle_outputs(dut, "out of reset, no clock")

    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    await Timer(10, unit="us")
    check_idle_outputs(dut, "out of reset, clock running")

    # The debouncers reset their outputs to 0 while the PS/2 lines idle high,
    # so there's a 0->1 settle on release; it must never look like a frame.
    await expect_no_valid(dut, 0.3)
    check_idle_outputs(dut, "after debounce settle")


@cocotb.test()
async def test_reset_during_transmission(dut):
    """Reset mid-frame recovers cleanly."""
    await reset_dut(dut)

    async def interrupted_frame():
        for bit in (0, 1, 0):
            await send_bit(dut.ps2_clk, dut.ps2_data, bit)
    cocotb.start_soon(interrupted_frame())
    await Timer(200, unit="us")

    await settle_offgrid(dut)
    dut.rst_n.value = 0
    await Timer(100, unit="ns")
    dut.rst_n.value = 1
    await Timer(10, unit="us")
    assert dut.interupt.value == 0, "interrupt should be cleared by reset"
    assert dut.data_rdy.value == 0, "FIFO should be empty after reset"

    await send_and_wait_valid(dut, 0x66)
    assert await read_byte(dut) == 0x66


# ===========================================================================
# Basic decode
# ===========================================================================

@cocotb.test()
async def ps2_decode_test(dut):
    """One byte: valid pulse, interrupt, data_rdy, read, clear."""
    await reset_dut(dut)
    await send_and_wait_valid(dut, 0xC2)

    assert dut.valid.value == 0, "valid must be a single-cycle pulse"
    assert dut.interupt.value == 1, "interrupt not set after valid"
    assert dut.data_rdy.value == 1, "data_rdy not set after valid"

    assert await read_byte(dut) == 0xC2
    assert dut.data_rdy.value == 0, "data_rdy not cleared after read"

    await pulse_clear_int(dut)
    assert dut.interupt.value == 0, "interrupt not cleared by clear_int"


@cocotb.test()
async def ps2_decode_second_test(dut):
    """A different byte value (0xF0, the break-code prefix)."""
    await reset_dut(dut)
    await send_and_wait_valid(dut, 0xF0)
    assert dut.interupt.value == 1
    assert await read_byte(dut) == 0xF0
    await pulse_clear_int(dut)
    assert dut.interupt.value == 0


@cocotb.test()
async def test_all_zeros(dut):
    await reset_dut(dut)
    await send_and_wait_valid(dut, 0x00)
    assert await read_byte(dut) == 0x00


@cocotb.test()
async def test_all_ones(dut):
    await reset_dut(dut)
    await send_and_wait_valid(dut, 0xFF)
    assert await read_byte(dut) == 0xFF


@cocotb.test()
async def test_variable_ps2_clock_fast(dut):
    """12 kHz PS/2 clock."""
    await reset_dut(dut)
    await send_and_wait_valid(dut, 0x77, half_us=42)
    assert await read_byte(dut) == 0x77


@cocotb.test()
async def test_variable_ps2_clock_slow(dut):
    """8 kHz PS/2 clock (below the 10 kHz spec minimum)."""
    await reset_dut(dut)
    await send_and_wait_valid(dut, 0x88, half_us=62)
    assert await read_byte(dut) == 0x88


# ===========================================================================
# Frame errors
# ===========================================================================

@cocotb.test()
async def ps2_decode_partial_test(dut):
    """An incomplete frame is ignored."""
    await reset_dut(dut)
    cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, 0xF0, bit_count=5,
                                parity_valid=False, stop_valid=False))
    await expect_no_valid(dut, 1.5)
    assert dut.data_rdy.value == 0


@cocotb.test()
async def test_parity_error(dut):
    await reset_dut(dut)
    cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, 0xAA, parity_valid=False))
    await expect_no_valid(dut, 1.5)
    assert dut.data_rdy.value == 0
    assert dut.uart_tx.value == 1, "no UART frame should be sent for a rejected byte"


@cocotb.test()
async def test_start_bit_error(dut):
    await reset_dut(dut)
    bits = frame_bits(0x55, start_bit=1)
    for bit in bits:
        await send_bit(dut.ps2_clk, dut.ps2_data, bit)
    dut.ps2_clk.value = 1
    await Timer(500, unit="us")
    assert dut.data_rdy.value == 0, "no data should be queued with a bad start bit"


@cocotb.test()
async def test_stop_bit_error(dut):
    await reset_dut(dut)
    cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, 0x33, stop_valid=False))
    await expect_no_valid(dut, 1.5)
    assert dut.data_rdy.value == 0


# ===========================================================================
# Debounce
# ===========================================================================

async def send_bits_glitched(dut, value, clk_glitch_bit=None, data_glitch_bit=None,
                             glitch_us=2):
    """Send a frame with a sub-debounce-window glitch injected.

    clk_glitch_bit:  during that bit's clock-high phase, pulse ps2_clk low for
                     glitch_us - an extra falling edge if not filtered.
    data_glitch_bit: invert ps2_data for glitch_us straddling that bit's clock
                     falling edge - the wrong value gets sampled if not filtered.
    """
    half = PS2_HALF_US
    for idx, bit in enumerate(frame_bits(value)):
        dut.ps2_data.value = bit
        dut.ps2_clk.value = 1
        if idx == clk_glitch_bit:
            await Timer(20, unit="us")
            dut.ps2_clk.value = 0
            await Timer(glitch_us, unit="us")
            dut.ps2_clk.value = 1
            await Timer(half - 20 - glitch_us, unit="us")
        elif idx == data_glitch_bit:
            await Timer(half - glitch_us // 2, unit="us")
            dut.ps2_data.value = bit ^ 1
            await Timer(glitch_us // 2, unit="us")
        else:
            await Timer(half, unit="us")
        dut.ps2_clk.value = 0
        if idx == data_glitch_bit:
            await Timer(glitch_us - glitch_us // 2, unit="us")
            dut.ps2_data.value = bit
            await Timer(half - (glitch_us - glitch_us // 2), unit="us")
        else:
            await Timer(half, unit="us")
    dut.ps2_clk.value = 1


@cocotb.test()
async def test_debounce_rejects_clock_glitch(dut):
    """A 2us glitch on ps2_clk mid-frame (well under the 128-cycle / 5.12us
    debounce window) must not be seen as an extra bit."""
    await reset_dut(dut)
    cocotb.start_soon(send_bits_glitched(dut, 0x5A, clk_glitch_bit=4))
    await RisingEdge(dut.valid)
    await Timer(80, unit="ns")
    assert await read_byte(dut) == 0x5A


@cocotb.test()
async def test_debounce_rejects_data_glitch(dut):
    """A 2us glitch on ps2_data straddling the sampling edge must not change
    the sampled bit."""
    await reset_dut(dut)
    cocotb.start_soon(send_bits_glitched(dut, 0x5A, data_glitch_bit=3))
    await RisingEdge(dut.valid)
    await Timer(80, unit="ns")
    assert await read_byte(dut) == 0x5A


# ===========================================================================
# Host interface: CS
# ===========================================================================

@cocotb.test()
async def test_cs_held_high(dut):
    """Holding cs high reads exactly one byte (no re-trigger)."""
    await reset_dut(dut)
    cocotb.start_soon(send_two_bytes(dut.ps2_clk, dut.ps2_data, 0x44, 0x55))
    await RisingEdge(dut.valid)
    await RisingEdge(dut.valid)
    await Timer(100, unit="us")

    assert dut.uio_oe.value == 0x00
    await settle_offgrid(dut)
    dut.cs.value = 1
    await Timer(1, unit="us")  # 25 cycles
    assert int(dut.uio_out.value) == 0x44
    await Timer(500, unit="ns")
    assert int(dut.uio_out.value) == 0x44, "value changed while cs held"
    assert dut.data_rdy.value == 1, "second byte should still be queued"
    dut.cs.value = 0
    await Timer(100, unit="ns")

    assert await read_byte(dut) == 0x55
    assert dut.data_rdy.value == 0


@cocotb.test()
async def test_cs_glitch_rejected(dut):
    """The glitch filter needs cs sampled high on two consecutive clock edges.
    A one-cycle cs pulse must not consume a byte; a two-cycle one must."""
    await reset_dut(dut)
    await send_and_wait_valid(dut, 0x3C)

    # 1 cycle high -> rejected
    await settle_offgrid(dut)
    dut.cs.value = 1
    await Timer(CLK_NS, unit="ns")
    dut.cs.value = 0
    await Timer(200, unit="ns")
    assert dut.data_rdy.value == 1, "1-cycle cs glitch consumed a byte"

    # 2 cycles high -> accepted (the minimum)
    await settle_offgrid(dut)
    dut.cs.value = 1
    await Timer(2 * CLK_NS, unit="ns")
    dut.cs.value = 0
    await Timer(200, unit="ns")
    assert dut.data_rdy.value == 0, "2-cycle cs pulse should have read the byte"
    assert int(dut.uio_out.value) == 0x3C


@cocotb.test()
async def test_empty_fifo_read(dut):
    """Reading with nothing queued (a polling host will do this) must not
    disturb the FIFO pointers or count."""
    await reset_dut(dut)

    assert await read_byte(dut) == 0x00, "data_out should still be its reset value"
    assert dut.data_rdy.value == 0, "empty read must not underflow count"
    assert dut.fifo_full.value == 0, "empty read must not underflow count"

    await send_and_wait_valid(dut, 0x1D)
    assert await read_byte(dut) == 0x1D
    assert dut.data_rdy.value == 0

    # Empty read again: bus holds the last value, state untouched
    assert await read_byte(dut) == 0x1D
    assert dut.data_rdy.value == 0
    assert dut.fifo_full.value == 0

    # If rd_ptr had advanced on the empty read, this byte would be misread
    await send_and_wait_valid(dut, 0x2E)
    assert await read_byte(dut) == 0x2E
    assert dut.data_rdy.value == 0


# ===========================================================================
# Interrupt semantics
# ===========================================================================

@cocotb.test()
async def ps2_decode_two_bytes_test(dut):
    await reset_dut(dut)
    cocotb.start_soon(send_two_bytes(dut.ps2_clk, dut.ps2_data, 0xF0, 0x15))
    await RisingEdge(dut.valid)
    await RisingEdge(dut.valid)
    await Timer(80, unit="ns")

    assert dut.valid.value == 0
    assert dut.interupt.value == 1
    assert dut.data_rdy.value == 1
    assert await read_byte(dut) == 0xF0
    assert await read_byte(dut) == 0x15
    assert dut.data_rdy.value == 0
    await pulse_clear_int(dut)
    assert dut.interupt.value == 0


@cocotb.test()
async def ps2_decode_two_bytes_int_clear_test(dut):
    """Interrupt-driven service: read, clear, interrupt re-asserts on the
    next byte."""
    await reset_dut(dut)
    cocotb.start_soon(send_two_bytes(dut.ps2_clk, dut.ps2_data, 0xF1, 0x16))

    await RisingEdge(dut.interupt)
    await Timer(1, unit="us")
    assert await read_byte(dut) == 0xF1
    assert dut.data_rdy.value == 0
    assert dut.interupt.value == 1, "interrupt is sticky until cleared"
    await pulse_clear_int(dut)
    assert dut.interupt.value == 0

    await RisingEdge(dut.interupt)
    await Timer(1, unit="us")
    assert await read_byte(dut) == 0x16
    assert dut.data_rdy.value == 0
    await pulse_clear_int(dut)
    assert dut.interupt.value == 0


@cocotb.test()
async def test_interrupt_with_queued_data(dut):
    """interupt is set by the *arrival* of a byte, not by FIFO occupancy. If
    two bytes land before the host services the first, clearing the interrupt
    after one read leaves the second byte queued with interupt low - the host
    must check data_rdy after clear_int. Pin that contract down."""
    await reset_dut(dut)
    cocotb.start_soon(send_two_bytes(dut.ps2_clk, dut.ps2_data, 0x1C, 0x32, gap_us=150))
    await RisingEdge(dut.valid)
    await RisingEdge(dut.valid)
    await Timer(80, unit="ns")
    assert dut.interupt.value == 1

    assert await read_byte(dut) == 0x1C
    await pulse_clear_int(dut)
    assert dut.interupt.value == 0, "interrupt cleared"
    assert dut.data_rdy.value == 1, "second byte still queued after clear_int"

    assert await read_byte(dut) == 0x32
    assert dut.data_rdy.value == 0
    assert dut.interupt.value == 0, "reading does not re-raise the interrupt"

    # Interrupt does re-assert on the next arrival
    await send_and_wait_valid(dut, 0x21)
    assert dut.interupt.value == 1

    # clear_int held high permanently: interrupt degenerates to a pulse
    await settle_offgrid(dut)
    dut.clear_int.value = 1
    await Timer(80, unit="ns")
    assert dut.interupt.value == 0
    cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, 0x22))
    await RisingEdge(dut.interupt)
    await Timer(3 * CLK_NS + 5, unit="ns")
    assert dut.interupt.value == 0, "with clear_int held, interupt should drop right away"
    dut.clear_int.value = 0
    assert dut.data_rdy.value == 1
    assert await read_byte(dut) == 0x21
    assert await read_byte(dut) == 0x22


# ===========================================================================
# FIFO
# ===========================================================================

@cocotb.test()
async def test_fifo_overflow(dut):
    """5 bytes with no reads: full flag set, 5th dropped, recovers after drain."""
    await reset_dut(dut)
    for i in range(FIFO_DEPTH + 1):
        cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, 0xA0 + i))
        await RisingEdge(dut.valid)
        await Timer(100, unit="us")
    assert dut.fifo_full.value == 1, "full flag should be set after 4 writes"

    for i in range(FIFO_DEPTH):
        assert await read_byte(dut) == 0xA0 + i
    assert dut.data_rdy.value == 0, "FIFO should be empty after 4 reads"
    assert dut.fifo_full.value == 0

    # The dropped byte is gone; the next one is accepted normally
    await send_and_wait_valid(dut, 0xB7)
    assert await read_byte(dut) == 0xB7
    assert dut.data_rdy.value == 0


@cocotb.test()
async def test_fifo_wraparound(dut):
    """Pointers wrap correctly: two full fill/drain cycles, then an
    interleaved pattern that puts the pointers at different offsets."""
    await reset_dut(dut)

    for rnd in range(2):
        for i in range(FIFO_DEPTH):
            await send_and_wait_valid(dut, 0x10 * (rnd + 1) + i)
        assert dut.fifo_full.value == 1
        for i in range(FIFO_DEPTH):
            assert await read_byte(dut) == 0x10 * (rnd + 1) + i
        assert dut.data_rdy.value == 0

    # write 3, read 2, write 3 (-> full), read 4
    for v in (0x31, 0x32, 0x33):
        await send_and_wait_valid(dut, v)
    assert await read_byte(dut) == 0x31
    assert await read_byte(dut) == 0x32
    for v in (0x34, 0x35, 0x36):
        await send_and_wait_valid(dut, v)
    assert dut.fifo_full.value == 1
    for v in (0x33, 0x34, 0x35, 0x36):
        assert await read_byte(dut) == v
    assert dut.data_rdy.value == 0
    assert dut.fifo_full.value == 0


@cocotb.test()
async def test_fifo_random_soak(dut):
    """Constrained-random producer/consumer with a scoreboard: random bytes,
    random legal PS/2 clock rates and inter-byte gaps, random host read
    timing. The producer never sends into a full FIFO, so the expected
    output is simply the input order. Reproduce with COCOTB_RANDOM_SEED."""
    await reset_dut(dut)
    n_bytes = 20
    sent = [random.randrange(256) for _ in range(n_bytes)]
    received = []

    async def producer():
        for value in sent:
            while dut.fifo_full.value == 1:
                await Timer(50, unit="us")
            half_us = random.randint(30, 62)      # 16.7 kHz .. 8 kHz
            await send_bits(dut.ps2_clk, dut.ps2_data, value, half_us=half_us)
            await Timer(random.randint(150, 400), unit="us")

    cocotb.start_soon(producer())

    deadline = Timer(n_bytes * 3, unit="ms")
    while len(received) < n_bytes:
        if dut.data_rdy.value == 1:
            await Timer(random.randint(0, 300), unit="us")
            received.append(await read_byte(dut))
        else:
            res = await First(RisingEdge(dut.data_rdy), deadline)
            assert isinstance(res, RisingEdge), \
                f"timed out with {len(received)}/{n_bytes} bytes received"

    assert received == sent, f"order/data mismatch:\n sent {sent}\n got  {received}"
    assert dut.data_rdy.value == 0
    assert dut.fifo_full.value == 0


@cocotb.test()
async def test_back_to_back_bytes(dut):
    """Three bytes with the minimum practical gap (150us; framing needs >100us
    of idle clock)."""
    await reset_dut(dut)

    async def rapid():
        await send_bits(dut.ps2_clk, dut.ps2_data, 0x1C)
        await Timer(150, unit="us")
        await send_bits(dut.ps2_clk, dut.ps2_data, 0x1B)
        await Timer(150, unit="us")
        await send_bits(dut.ps2_clk, dut.ps2_data, 0x23)
    cocotb.start_soon(rapid())

    for _ in range(3):
        await RisingEdge(dut.valid)
    await Timer(100, unit="us")
    for v in (0x1C, 0x1B, 0x23):
        assert await read_byte(dut) == v


# ===========================================================================
# UART debug output
# ===========================================================================

@cocotb.test()
async def test_uart_tx_single_byte(dut):
    """Each decoded byte is echoed on uo[4] as <status><data> at 115200 8N1.
    status = {0000, fifo_full, data_rdy, interupt, 1}, captured 2 cycles after
    valid so the flags reflect the byte just queued."""
    await reset_dut(dut)
    cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, 0xAB))
    status, data = await uart_receive_frame(dut)
    assert status == uart_status(fifo_full=0, data_rdy=1, interupt=1), f"status {status:#04x}"
    assert data == 0xAB, f"data {data:#04x}"
    # Line returns to idle and stays there
    await Timer(50, unit="us")
    assert dut.uart_tx.value == 1


@cocotb.test()
async def test_uart_tx_multiple_bytes(dut):
    await reset_dut(dut)
    for i, value in enumerate((0x1C, 0x23, 0x3A)):
        cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, value))
        status, data = await uart_receive_frame(dut)
        assert data == value, f"byte {i}: data {data:#04x} != {value:#04x}"
        assert status == uart_status(fifo_full=0, data_rdy=1, interupt=1), f"byte {i}: status {status:#04x}"
        await Timer(150, unit="us")
    # Nothing was read by the host; all three should still be queued in order
    for value in (0x1C, 0x23, 0x3A):
        assert await read_byte(dut) == value


@cocotb.test()
async def test_uart_tx_fifo_full_status(dut):
    """The 5th byte into a full FIFO is dropped from the FIFO but still
    echoed on the UART, with fifo_full set in its status byte."""
    await reset_dut(dut)
    for i in range(FIFO_DEPTH):
        cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, 0x10 + i))
        status, data = await uart_receive_frame(dut)
        assert data == 0x10 + i
        expect_full = 1 if i == FIFO_DEPTH - 1 else 0
        assert status == uart_status(fifo_full=expect_full, data_rdy=1, interupt=1), \
            f"byte {i}: status {status:#04x}"
        await Timer(150, unit="us")

    cocotb.start_soon(send_bits(dut.ps2_clk, dut.ps2_data, 0x99))
    status, data = await uart_receive_frame(dut)
    assert data == 0x99
    assert status == uart_status(fifo_full=1, data_rdy=1, interupt=1), f"status {status:#04x}"

    for i in range(FIFO_DEPTH):
        assert await read_byte(dut) == 0x10 + i
    assert dut.data_rdy.value == 0, "the dropped byte must not appear"
