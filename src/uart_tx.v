/*
 * Copyright (c) 2024 Ben Payne
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module uart_tx #(
    parameter CLKS_PER_BIT = 217  // 25MHz / 115200 baud
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       tx_start,     // Pulse to start transmission
    input  wire [7:0] tx_data,      // Data byte to transmit
    output reg        tx,           // UART TX line
    output reg        tx_busy       // High when transmitting
);

    // UART frame: 1 start bit (0) + 8 data bits + 1 stop bit (1)
    localparam IDLE  = 0;
    localparam START = 1;
    localparam DATA  = 2;
    localparam STOP  = 3;

    reg [1:0]  state;
    reg [15:0] clk_count;
    reg [2:0]  bit_index;
    reg [7:0]  tx_data_reg;

    always @(posedge clk) begin
        if (rst) begin
            state       <= IDLE;
            tx          <= 1'b1;  // Idle high
            tx_busy     <= 1'b0;
            clk_count   <= 16'd0;
            bit_index   <= 3'd0;
            tx_data_reg <= 8'd0;
        end else begin
            case (state)
                IDLE: begin
                    tx        <= 1'b1;  // Idle high
                    tx_busy   <= 1'b0;
                    clk_count <= 16'd0;
                    bit_index <= 3'd0;

                    if (tx_start) begin
                        tx_data_reg <= tx_data;
                        tx_busy     <= 1'b1;
                        state       <= START;
                    end
                end

                START: begin
                    tx <= 1'b0;  // Start bit
                    if (clk_count < CLKS_PER_BIT - 1) begin
                        clk_count <= clk_count + 1;
                    end else begin
                        clk_count <= 16'd0;
                        state     <= DATA;
                    end
                end

                DATA: begin
                    tx <= tx_data_reg[bit_index];
                    if (clk_count < CLKS_PER_BIT - 1) begin
                        clk_count <= clk_count + 1;
                    end else begin
                        clk_count <= 16'd0;
                        if (bit_index < 7) begin
                            bit_index <= bit_index + 1;
                        end else begin
                            bit_index <= 3'd0;
                            state     <= STOP;
                        end
                    end
                end

                STOP: begin
                    tx <= 1'b1;  // Stop bit
                    if (clk_count < CLKS_PER_BIT - 1) begin
                        clk_count <= clk_count + 1;
                    end else begin
                        clk_count <= 16'd0;
                        state     <= IDLE;
                    end
                end

                default: state <= IDLE;
            endcase
        end
    end

endmodule
