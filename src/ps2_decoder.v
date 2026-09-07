/*
 * Copyright (c) 2024 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// PS/2 Keyboard Decoder
//
// Detects falling edges of ps2_clk, shifts in 11 bits (start + 8 data +
// parity + stop), validates the frame (start=0, odd parity, stop=1) and
// pulses `valid` for one clock when a complete, valid byte is received.
// A sticky `interrupt` flag is set on `valid` and cleared by `int_clear`.
module ps2_decoder #(
    parameter CLK_FREQ_HZ = 25_000_000,  // System clock frequency in Hz
    parameter PS2_CLK_HZ  = 10_000       // PS/2 clock frequency (10-16.7 kHz per spec)
) (
    input  wire       clk,        // System clock
    input  wire       reset,      // Active-high reset
    input  wire       ps2_clk,    // PS/2 clock (should be debounced)
    input  wire       ps2_data,   // PS/2 data (should be debounced)
    input  wire       int_clear,  // Clear interrupt flag
    output wire       valid,      // Single-cycle pulse when byte received
    output wire       interupt,   // Sticky interrupt flag (cleared by int_clear)
    output wire [7:0] data        // Decoded scan code byte
);

  localparam PS2_BIT_TIME = CLK_FREQ_HZ / PS2_CLK_HZ;  // Timeout for slowest clock

  localparam IDLE = 0, SETUP = 1, CLEAR = 2;

  reg [10:0] shift_reg;
  reg [12:0] clk_timeout;
  reg [ 2:0] state_reg;
  reg [ 7:0] ps2_value;
  reg        valid_reg;
  reg        int_reg;
  reg        ps2_clk_prev;
  reg        shift_reset;

  assign data     = ps2_value;
  assign valid    = valid_reg;
  assign interupt = int_reg;

  // Shift register: captures 11 bits on falling edge of ps2_clk
  always @(posedge clk or posedge reset) begin
    if (reset) begin
      shift_reg <= 11'b11111111111;
      ps2_clk_prev <= 0;
    end else begin
      if (shift_reset) begin
        shift_reg <= 11'b11111111111;
      end else begin
        ps2_clk_prev <= ps2_clk;
        // Detect falling edge of ps2_clk
        if (!ps2_clk && ps2_clk_prev) begin
          shift_reg <= {shift_reg[9:0], ps2_data};
        end
      end
    end
  end

  // State machine: validates frame and outputs data
  always @(posedge clk or posedge reset) begin
    if (reset) begin
      clk_timeout <= 0;
      state_reg   <= IDLE;
      valid_reg   <= 0;
      shift_reset <= 0;
      ps2_value   <= 8'b00000000;
    end else begin
      if (ps2_clk) begin
        // Use >= to support PS/2 clocks faster than 10kHz (spec allows 10-16.7kHz)
        if (clk_timeout >= PS2_BIT_TIME[12:0]) begin
          case (state_reg)
            IDLE: begin
              // Extract data bits (LSB first, bits 1-8 of frame)
              ps2_value <= {
                shift_reg[2], shift_reg[3], shift_reg[4], shift_reg[5],
                shift_reg[6], shift_reg[7], shift_reg[8], shift_reg[9]
              };
              state_reg <= SETUP;
            end
            SETUP: begin
              shift_reset <= 1;
              // Validate frame:
              // - Start bit (shift_reg[10]) must be 0
              // - Odd parity (shift_reg[1] XOR data bits)
              // - Stop bit (shift_reg[0]) must be 1
              valid_reg <= (shift_reg[2] ^ shift_reg[3] ^ shift_reg[4] ^ shift_reg[5] ^
                            shift_reg[6] ^ shift_reg[7] ^ shift_reg[8] ^ shift_reg[9] ^
                            shift_reg[1]) && shift_reg[0] == 1 && shift_reg[10] == 0;
              state_reg <= CLEAR;
            end
            CLEAR: begin
              shift_reset <= 0;
              valid_reg   <= 0;
              ps2_value   <= 0;
              clk_timeout[12] <= 1;
            end
          endcase
        end else if (clk_timeout[12] == 0) begin
          clk_timeout <= clk_timeout + 1;
        end
      end else begin
        clk_timeout <= 0;
        state_reg   <= IDLE;
        valid_reg   <= 0;
      end
    end
  end

  // Interrupt flag: sticky, set on valid, cleared by int_clear
  always @(posedge clk or posedge reset) begin
    if (reset) begin
      int_reg <= 0;
    end else if (valid_reg) begin
      int_reg <= 1;
    end else if (int_clear) begin
      int_reg <= 0;
    end
  end

endmodule
