/*
 * Copyright (c) 2024 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Input Debouncer with 2-FF Synchronizer
//
// Provides metastability protection for asynchronous inputs by using a
// 2-flip-flop synchronizer followed by debounce logic. The debounce logic
// requires the input to be stable for DEBOUNCE_CYCLES system clock cycles
// before the output changes.
module debounce #(
    parameter DEBOUNCE_CYCLES = 128  // Number of stable clocks required (2^n recommended)
) (
    input  wire clk,              // System clock
    input  wire reset,            // Active-high reset
    input  wire button,           // Asynchronous input signal
    output wire debounced_button  // Synchronized and debounced output
);

  // 2-FF synchronizer to prevent metastability
  // The first FF may capture a metastable state, but the second
  // FF will have resolved to a stable value by the time it's read
  reg sync_ff1;
  reg sync_ff2;

  // Debounce counter - counts stable cycles
  localparam COUNTER_WIDTH = $clog2(DEBOUNCE_CYCLES + 1);
  reg [COUNTER_WIDTH-1:0] counter;

  reg debounced_button_reg;
  reg last_button;

  assign debounced_button = debounced_button_reg;

  // First stage: 2-FF synchronizer
  always @(posedge clk or posedge reset) begin
    if (reset) begin
      sync_ff1 <= 0;
      sync_ff2 <= 0;
    end else begin
      sync_ff1 <= button;
      sync_ff2 <= sync_ff1;  // Second stage ensures stability
    end
  end

  // Second stage: Debounce logic using synchronized signal
  always @(posedge clk or posedge reset) begin
    if (reset) begin
      counter <= 0;
      debounced_button_reg <= 0;
      last_button <= 0;
    end else begin
      last_button <= sync_ff2;  // Use synchronized signal

      if (sync_ff2 != last_button) begin
        // Input changed - reset counter
        counter <= 0;
      end else if (counter == DEBOUNCE_CYCLES - 1) begin
        // Input stable for required cycles - update output
        debounced_button_reg <= last_button;
      end else begin
        // Still counting stable cycles
        counter <= counter + 1;
      end
    end
  end

endmodule
