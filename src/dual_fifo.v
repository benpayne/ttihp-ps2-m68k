/*
 * Copyright (c) 2024 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

// Dual-Port Synchronous FIFO
//
// Simple synchronous FIFO with separate read and write ports. Supports
// simultaneous read and write operations.
module dual_fifo #(
    parameter DEPTH = 2,  // 2^DEPTH number of entries
    parameter WIDTH = 8   // Data width in bits
) (
    input  wire             clk,      // System clock
    input  wire             rst,      // Active-high reset
    input  wire             wr_en,    // Write enable (pulse to write)
    input  wire             rd_en,    // Read enable (pulse to read)
    input  wire [WIDTH-1:0] data_in,  // Data to write
    output reg  [WIDTH-1:0] data_out, // Data read (valid 1 cycle after rd_en)
    output wire             empty,    // FIFO empty flag (1 = empty)
    output wire             full      // FIFO full flag (1 = full)
);

  // Internal storage
  reg [WIDTH-1:0] mem[0:(1<<DEPTH)-1];

  // Pointers and count
  reg [DEPTH-1:0] wr_ptr;  // Write pointer
  reg [DEPTH-1:0] rd_ptr;  // Read pointer
  reg [  DEPTH:0] count;   // Entry count (DEPTH+1 bits to detect full)

  // Status flags
  assign empty = (count == 0);
  assign full  = count[DEPTH];  // MSB set when count >= 2^DEPTH

  // Write operation
  always @(posedge clk or posedge rst) begin
    if (rst) begin
      wr_ptr <= 0;
    end else if (wr_en && !full) begin
      mem[wr_ptr] <= data_in;
      wr_ptr <= wr_ptr + 1;
    end
  end

  // Read operation
  always @(posedge clk or posedge rst) begin
    if (rst) begin
      rd_ptr <= 0;
      data_out <= 0;
    end else if (rd_en && !empty) begin
      data_out <= mem[rd_ptr];
      rd_ptr <= rd_ptr + 1;
    end
  end

  // Entry counter
  always @(posedge clk or posedge rst) begin
    if (rst) begin
      count <= 0;
    end else begin
      case ({
        wr_en && !full, rd_en && !empty
      })
        2'b01: count <= count - 1;  // Read only
        2'b10: count <= count + 1;  // Write only
        2'b11: count <= count;  // Simultaneous read/write
        default: count <= count;  // No operation
      endcase
    end
  end

endmodule
