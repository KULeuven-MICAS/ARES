// Copyright 2026 KU Leuven.
// Licensed under the Apache License, Version 2.0, see LICENSE for details.
// SPDX-License-Identifier: Apache-2.0

// Sum of absolute differences (SAD) of four 8-bit pixel pairs.
//
// ARES example design: a small clocked datapath with an active-low reset.
// The result register loads when valid_in is high; valid_out follows
// valid_in with one cycle of delay. The code is written in a direct style,
// so an optimizer has room to improve area, power, and delay.
module sad4 (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        valid_in,
    input  wire [31:0] a,          // four pixels: a[7:0], a[15:8], ...
    input  wire [31:0] b,          // four pixels: b[7:0], b[15:8], ...
    output reg  [9:0]  sad,
    output reg         valid_out
);

    wire [7:0] a0 = a[7:0];
    wire [7:0] a1 = a[15:8];
    wire [7:0] a2 = a[23:16];
    wire [7:0] a3 = a[31:24];
    wire [7:0] b0 = b[7:0];
    wire [7:0] b1 = b[15:8];
    wire [7:0] b2 = b[23:16];
    wire [7:0] b3 = b[31:24];

    // Absolute difference of each pair: a comparator and two subtractors.
    wire [7:0] d0 = (a0 > b0) ? (a0 - b0) : (b0 - a0);
    wire [7:0] d1 = (a1 > b1) ? (a1 - b1) : (b1 - a1);
    wire [7:0] d2 = (a2 > b2) ? (a2 - b2) : (b2 - a2);
    wire [7:0] d3 = (a3 > b3) ? (a3 - b3) : (b3 - a3);

    // Sum of the four differences (at most 4 * 255 = 1020).
    wire [9:0] sum = d0 + d1 + d2 + d3;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sad       <= 10'd0;
            valid_out <= 1'b0;
        end else begin
            valid_out <= valid_in;
            if (valid_in)
                sad <= sum;
        end
    end

endmodule
