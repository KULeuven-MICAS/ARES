// NangateOpenCellLibrary.v
// Auto-generated from NangateOpenCellLibrary_typical.lib by gen_nangate45_verilog.py
// Functional (zero-delay) behavioral models for gate-level simulation with iverilog.
// Copyright (C) 2004-2011 Nangate Inc.  All rights reserved.
// This file is for simulation only — do not use for synthesis.

`timescale 1ns/1ps
module AND2_X1 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = ((A1 & A2));
endmodule

module AND2_X2 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = ((A1 & A2));
endmodule

module AND2_X4 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = ((A1 & A2));
endmodule

module AND3_X1 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (((A1 & A2) & A3));
endmodule

module AND3_X2 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (((A1 & A2) & A3));
endmodule

module AND3_X4 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (((A1 & A2) & A3));
endmodule

module AND4_X1 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = ((((A1 & A2) & A3) & A4));
endmodule

module AND4_X2 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = ((((A1 & A2) & A3) & A4));
endmodule

module AND4_X4 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = ((((A1 & A2) & A3) & A4));
endmodule

module ANTENNA_X1 (A);
  input A;
endmodule

module AOI21_X1 (A, B1, B2, ZN);
  input  A, B1, B2;
  output ZN;
  assign ZN = (!(A | (B1 & B2)));
endmodule

module AOI21_X2 (A, B1, B2, ZN);
  input  A, B1, B2;
  output ZN;
  assign ZN = (!(A | (B1 & B2)));
endmodule

module AOI21_X4 (A, B1, B2, ZN);
  input  A, B1, B2;
  output ZN;
  assign ZN = (!(A | (B1 & B2)));
endmodule

module AOI22_X1 (A1, A2, B1, B2, ZN);
  input  A1, A2, B1, B2;
  output ZN;
  assign ZN = (!((A1 & A2) | (B1 & B2)));
endmodule

module AOI22_X2 (A1, A2, B1, B2, ZN);
  input  A1, A2, B1, B2;
  output ZN;
  assign ZN = (!((A1 & A2) | (B1 & B2)));
endmodule

module AOI22_X4 (A1, A2, B1, B2, ZN);
  input  A1, A2, B1, B2;
  output ZN;
  assign ZN = (!((A1 & A2) | (B1 & B2)));
endmodule

module AOI211_X1 (A, B, C1, C2, ZN);
  input  A, B, C1, C2;
  output ZN;
  assign ZN = (!(((C1 & C2) | B) | A));
endmodule

module AOI211_X2 (A, B, C1, C2, ZN);
  input  A, B, C1, C2;
  output ZN;
  assign ZN = (!(((C1 & C2) | B) | A));
endmodule

module AOI211_X4 (A, B, C1, C2, ZN);
  input  A, B, C1, C2;
  output ZN;
  assign ZN = (!(!(!(((C1 & C2) | B) | A))));
endmodule

module AOI221_X1 (A, B1, B2, C1, C2, ZN);
  input  A, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(((C1 & C2) | A) | (B1 & B2)));
endmodule

module AOI221_X2 (A, B1, B2, C1, C2, ZN);
  input  A, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(((C1 & C2) | A) | (B1 & B2)));
endmodule

module AOI221_X4 (A, B1, B2, C1, C2, ZN);
  input  A, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(!(!(((C1 & C2) | A) | (B1 & B2)))));
endmodule

module AOI222_X1 (A1, A2, B1, B2, C1, C2, ZN);
  input  A1, A2, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(((A1 & A2) | (B1 & B2)) | (C1 & C2)));
endmodule

module AOI222_X2 (A1, A2, B1, B2, C1, C2, ZN);
  input  A1, A2, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(((A1 & A2) | (B1 & B2)) | (C1 & C2)));
endmodule

module AOI222_X4 (A1, A2, B1, B2, C1, C2, ZN);
  input  A1, A2, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(!(!(((A1 & A2) | (B1 & B2)) | (C1 & C2)))));
endmodule

module BUF_X1 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module BUF_X2 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module BUF_X4 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module BUF_X8 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module BUF_X16 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module BUF_X32 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module CLKBUF_X1 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module CLKBUF_X2 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module CLKBUF_X3 (A, Z);
  input  A;
  output Z;
  assign Z = (A);
endmodule

module CLKGATETST_X1 (CK, E, SE, GCK);
  input  CK, E, SE;
  output GCK;
  reg    latch_en;
  always @(CK or (E | SE))
    if (~CK) latch_en <= (E | SE);
  assign GCK = CK & latch_en;
endmodule

module CLKGATETST_X2 (CK, E, SE, GCK);
  input  CK, E, SE;
  output GCK;
  reg    latch_en;
  always @(CK or (E | SE))
    if (~CK) latch_en <= (E | SE);
  assign GCK = CK & latch_en;
endmodule

module CLKGATETST_X4 (CK, E, SE, GCK);
  input  CK, E, SE;
  output GCK;
  reg    latch_en;
  always @(CK or (E | SE))
    if (~CK) latch_en <= (E | SE);
  assign GCK = CK & latch_en;
endmodule

module CLKGATETST_X8 (CK, E, SE, GCK);
  input  CK, E, SE;
  output GCK;
  reg    latch_en;
  always @(CK or (E | SE))
    if (~CK) latch_en <= (E | SE);
  assign GCK = CK & latch_en;
endmodule

module CLKGATE_X1 (CK, E, GCK);
  input  CK, E;
  output GCK;
  reg    latch_en;
  always @(CK or E)
    if (~CK) latch_en <= E;
  assign GCK = CK & latch_en;
endmodule

module CLKGATE_X2 (CK, E, GCK);
  input  CK, E;
  output GCK;
  reg    latch_en;
  always @(CK or E)
    if (~CK) latch_en <= E;
  assign GCK = CK & latch_en;
endmodule

module CLKGATE_X4 (CK, E, GCK);
  input  CK, E;
  output GCK;
  reg    latch_en;
  always @(CK or E)
    if (~CK) latch_en <= E;
  assign GCK = CK & latch_en;
endmodule

module CLKGATE_X8 (CK, E, GCK);
  input  CK, E;
  output GCK;
  reg    latch_en;
  always @(CK or E)
    if (~CK) latch_en <= E;
  assign GCK = CK & latch_en;
endmodule

module DFFRS_X1 (D, RN, SN, CK, Q, QN);
  input  D, RN, SN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge RN or negedge SN) begin
    if (~RN) begin
      Q  <= 1'b0;
      QN <= 1'b1;
    end else if (~SN) begin
      Q  <= 1'b1;
      QN <= 1'b0;
    end else begin
      Q  <= (D);
      QN <= ~(D);
    end
  end
endmodule

module DFFRS_X2 (D, RN, SN, CK, Q, QN);
  input  D, RN, SN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge RN or negedge SN) begin
    if (~RN) begin
      Q  <= 1'b0;
      QN <= 1'b1;
    end else if (~SN) begin
      Q  <= 1'b1;
      QN <= 1'b0;
    end else begin
      Q  <= (D);
      QN <= ~(D);
    end
  end
endmodule

module DFFR_X1 (D, RN, CK, Q, QN);
  input  D, RN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge RN) begin
    if (~RN) begin
      Q  <= 1'b0;
      QN <= 1'b1;
    end else begin
      Q  <= (D);
      QN <= ~(D);
    end
  end
endmodule

module DFFR_X2 (D, RN, CK, Q, QN);
  input  D, RN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge RN) begin
    if (~RN) begin
      Q  <= 1'b0;
      QN <= 1'b1;
    end else begin
      Q  <= (D);
      QN <= ~(D);
    end
  end
endmodule

module DFFS_X1 (D, SN, CK, Q, QN);
  input  D, SN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge SN) begin
    if (~SN) begin
      Q  <= 1'b1;
      QN <= 1'b0;
    end else begin
      Q  <= (D);
      QN <= ~(D);
    end
  end
endmodule

module DFFS_X2 (D, SN, CK, Q, QN);
  input  D, SN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge SN) begin
    if (~SN) begin
      Q  <= 1'b1;
      QN <= 1'b0;
    end else begin
      Q  <= (D);
      QN <= ~(D);
    end
  end
endmodule

module DFF_X1 (D, CK, Q, QN);
  input  D, CK;
  output reg Q, QN;
  always @(posedge CK) begin
    Q  <= (D);
    QN <= ~(D);
  end
endmodule

module DFF_X2 (D, CK, Q, QN);
  input  D, CK;
  output reg Q, QN;
  always @(posedge CK) begin
    Q  <= (D);
    QN <= ~(D);
  end
endmodule

module DLH_X1 (D, G, Q);
  input  D, G;
  output reg Q;
  always @(*) begin
    if (G) begin
      Q  = (D);
    end
  end
endmodule

module DLH_X2 (D, G, Q);
  input  D, G;
  output reg Q;
  always @(*) begin
    if (G) begin
      Q  = (D);
    end
  end
endmodule

module DLL_X1 (D, GN, Q);
  input  D, GN;
  output reg Q;
  always @(*) begin
    if (~GN) begin
      Q  = (D);
    end
  end
endmodule

module DLL_X2 (D, GN, Q);
  input  D, GN;
  output reg Q;
  always @(*) begin
    if (~GN) begin
      Q  = (D);
    end
  end
endmodule

module FA_X1 (A, B, CI, CO, S);
  input  A, B, CI;
  output CO;
  assign CO = (((A & B) | (CI & (A | B))));
  output S;
  assign S = ((CI ^ (A ^ B)));
endmodule

module FILLCELL_X1 ();
endmodule

module TAPCELL_X1 ();
endmodule

module FILLCELL_X2 ();
endmodule

module FILLCELL_X4 ();
endmodule

module FILLCELL_X8 ();
endmodule

module FILLCELL_X16 ();
endmodule

module FILLCELL_X32 ();
endmodule

module HA_X1 (A, B, CO, S);
  input  A, B;
  output CO;
  assign CO = ((A & B));
  output S;
  assign S = ((A ^ B));
endmodule

module INV_X1 (A, ZN);
  input  A;
  output ZN;
  assign ZN = (!A);
endmodule

module INV_X2 (A, ZN);
  input  A;
  output ZN;
  assign ZN = (!A);
endmodule

module INV_X4 (A, ZN);
  input  A;
  output ZN;
  assign ZN = (!A);
endmodule

module INV_X8 (A, ZN);
  input  A;
  output ZN;
  assign ZN = (!A);
endmodule

module INV_X16 (A, ZN);
  input  A;
  output ZN;
  assign ZN = (!A);
endmodule

module INV_X32 (A, ZN);
  input  A;
  output ZN;
  assign ZN = (!A);
endmodule

module LOGIC0_X1 (Z);
  output Z;
  assign Z = (0);
endmodule

module LOGIC1_X1 (Z);
  output Z;
  assign Z = (1);
endmodule

module MUX2_X1 (A, B, S, Z);
  input  A, B, S;
  output Z;
  assign Z = (((S & B) | (A & !S)));
endmodule

module MUX2_X2 (A, B, S, Z);
  input  A, B, S;
  output Z;
  assign Z = (((S & B) | (A & !S)));
endmodule

module NAND2_X1 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = (!(A1 & A2));
endmodule

module NAND2_X2 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = (!(A1 & A2));
endmodule

module NAND2_X4 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = (!(A1 & A2));
endmodule

module NAND3_X1 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (!((A1 & A2) & A3));
endmodule

module NAND3_X2 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (!((A1 & A2) & A3));
endmodule

module NAND3_X4 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (!((A1 & A2) & A3));
endmodule

module NAND4_X1 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = (!(((A1 & A2) & A3) & A4));
endmodule

module NAND4_X2 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = (!(((A1 & A2) & A3) & A4));
endmodule

module NAND4_X4 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = (!(((A1 & A2) & A3) & A4));
endmodule

module NOR2_X1 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = (!(A1 | A2));
endmodule

module NOR2_X2 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = (!(A1 | A2));
endmodule

module NOR2_X4 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = (!(A1 | A2));
endmodule

module NOR3_X1 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (!((A1 | A2) | A3));
endmodule

module NOR3_X2 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (!((A1 | A2) | A3));
endmodule

module NOR3_X4 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (!((A1 | A2) | A3));
endmodule

module NOR4_X1 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = (!(((A1 | A2) | A3) | A4));
endmodule

module NOR4_X2 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = (!(((A1 | A2) | A3) | A4));
endmodule

module NOR4_X4 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = (!(((A1 | A2) | A3) | A4));
endmodule

module OAI21_X1 (A, B1, B2, ZN);
  input  A, B1, B2;
  output ZN;
  assign ZN = (!(A & (B1 | B2)));
endmodule

module OAI21_X2 (A, B1, B2, ZN);
  input  A, B1, B2;
  output ZN;
  assign ZN = (!(A & (B1 | B2)));
endmodule

module OAI21_X4 (A, B1, B2, ZN);
  input  A, B1, B2;
  output ZN;
  assign ZN = (!(A & (B1 | B2)));
endmodule

module OAI22_X1 (A1, A2, B1, B2, ZN);
  input  A1, A2, B1, B2;
  output ZN;
  assign ZN = (!((A1 | A2) & (B1 | B2)));
endmodule

module OAI22_X2 (A1, A2, B1, B2, ZN);
  input  A1, A2, B1, B2;
  output ZN;
  assign ZN = (!((A1 | A2) & (B1 | B2)));
endmodule

module OAI22_X4 (A1, A2, B1, B2, ZN);
  input  A1, A2, B1, B2;
  output ZN;
  assign ZN = (!((A1 | A2) & (B1 | B2)));
endmodule

module OAI33_X1 (A1, A2, A3, B1, B2, B3, ZN);
  input  A1, A2, A3, B1, B2, B3;
  output ZN;
  assign ZN = (!(((A1 | A2) | A3) & ((B1 | B2) | B3)));
endmodule

module OAI211_X1 (A, B, C1, C2, ZN);
  input  A, B, C1, C2;
  output ZN;
  assign ZN = (!(((C1 | C2) & A) & B));
endmodule

module OAI211_X2 (A, B, C1, C2, ZN);
  input  A, B, C1, C2;
  output ZN;
  assign ZN = (!(((C1 | C2) & A) & B));
endmodule

module OAI211_X4 (A, B, C1, C2, ZN);
  input  A, B, C1, C2;
  output ZN;
  assign ZN = (!(((C1 | C2) & A) & B));
endmodule

module OAI221_X1 (A, B1, B2, C1, C2, ZN);
  input  A, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(((C1 | C2) & A) & (B1 | B2)));
endmodule

module OAI221_X2 (A, B1, B2, C1, C2, ZN);
  input  A, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(((C1 | C2) & A) & (B1 | B2)));
endmodule

module OAI221_X4 (A, B1, B2, C1, C2, ZN);
  input  A, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(!(!(((C1 | C2) & A) & (B1 | B2)))));
endmodule

module OAI222_X1 (A1, A2, B1, B2, C1, C2, ZN);
  input  A1, A2, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(((A1 | A2) & (B1 | B2)) & (C1 | C2)));
endmodule

module OAI222_X2 (A1, A2, B1, B2, C1, C2, ZN);
  input  A1, A2, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(((A1 | A2) & (B1 | B2)) & (C1 | C2)));
endmodule

module OAI222_X4 (A1, A2, B1, B2, C1, C2, ZN);
  input  A1, A2, B1, B2, C1, C2;
  output ZN;
  assign ZN = (!(!(!(((A1 | A2) & (B1 | B2)) & (C1 | C2)))));
endmodule

module OR2_X1 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = ((A1 | A2));
endmodule

module OR2_X2 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = ((A1 | A2));
endmodule

module OR2_X4 (A1, A2, ZN);
  input  A1, A2;
  output ZN;
  assign ZN = ((A1 | A2));
endmodule

module OR3_X1 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (((A1 | A2) | A3));
endmodule

module OR3_X2 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (((A1 | A2) | A3));
endmodule

module OR3_X4 (A1, A2, A3, ZN);
  input  A1, A2, A3;
  output ZN;
  assign ZN = (((A1 | A2) | A3));
endmodule

module OR4_X1 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = ((((A1 | A2) | A3) | A4));
endmodule

module OR4_X2 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = ((((A1 | A2) | A3) | A4));
endmodule

module OR4_X4 (A1, A2, A3, A4, ZN);
  input  A1, A2, A3, A4;
  output ZN;
  assign ZN = ((((A1 | A2) | A3) | A4));
endmodule

module SDFFRS_X1 (D, RN, SE, SI, SN, CK, Q, QN);
  input  D, RN, SE, SI, SN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge RN or negedge SN) begin
    if (~RN) begin
      Q  <= 1'b0;
      QN <= 1'b1;
    end else if (~SN) begin
      Q  <= 1'b1;
      QN <= 1'b0;
    end else begin
      Q  <= (((SE  &  SI)  |  (D  &  !SE)));
      QN <= ~(((SE  &  SI)  |  (D  &  !SE)));
    end
  end
endmodule

module SDFFRS_X2 (D, RN, SE, SI, SN, CK, Q, QN);
  input  D, RN, SE, SI, SN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge RN or negedge SN) begin
    if (~RN) begin
      Q  <= 1'b0;
      QN <= 1'b1;
    end else if (~SN) begin
      Q  <= 1'b1;
      QN <= 1'b0;
    end else begin
      Q  <= (((SE  &  SI)  |  (D  &  !SE)));
      QN <= ~(((SE  &  SI)  |  (D  &  !SE)));
    end
  end
endmodule

module SDFFR_X1 (D, RN, SE, SI, CK, Q, QN);
  input  D, RN, SE, SI, CK;
  output reg Q, QN;
  always @(posedge CK or negedge RN) begin
    if (~RN) begin
      Q  <= 1'b0;
      QN <= 1'b1;
    end else begin
      Q  <= (((SE  &  SI)  |  (D  &  !SE)));
      QN <= ~(((SE  &  SI)  |  (D  &  !SE)));
    end
  end
endmodule

module SDFFR_X2 (D, RN, SE, SI, CK, Q, QN);
  input  D, RN, SE, SI, CK;
  output reg Q, QN;
  always @(posedge CK or negedge RN) begin
    if (~RN) begin
      Q  <= 1'b0;
      QN <= 1'b1;
    end else begin
      Q  <= (((SE  &  SI)  |  (D  &  !SE)));
      QN <= ~(((SE  &  SI)  |  (D  &  !SE)));
    end
  end
endmodule

module SDFFS_X1 (D, SE, SI, SN, CK, Q, QN);
  input  D, SE, SI, SN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge SN) begin
    if (~SN) begin
      Q  <= 1'b1;
      QN <= 1'b0;
    end else begin
      Q  <= (((SE  &  SI)  |  (D  &  !SE)));
      QN <= ~(((SE  &  SI)  |  (D  &  !SE)));
    end
  end
endmodule

module SDFFS_X2 (D, SE, SI, SN, CK, Q, QN);
  input  D, SE, SI, SN, CK;
  output reg Q, QN;
  always @(posedge CK or negedge SN) begin
    if (~SN) begin
      Q  <= 1'b1;
      QN <= 1'b0;
    end else begin
      Q  <= (((SE  &  SI)  |  (D  &  !SE)));
      QN <= ~(((SE  &  SI)  |  (D  &  !SE)));
    end
  end
endmodule

module SDFF_X1 (D, SE, SI, CK, Q, QN);
  input  D, SE, SI, CK;
  output reg Q, QN;
  always @(posedge CK) begin
    Q  <= (((SE  &  SI)  |  (D  &  !SE)));
    QN <= ~(((SE  &  SI)  |  (D  &  !SE)));
  end
endmodule

module SDFF_X2 (D, SE, SI, CK, Q, QN);
  input  D, SE, SI, CK;
  output reg Q, QN;
  always @(posedge CK) begin
    Q  <= (((SE  &  SI)  |  (D  &  !SE)));
    QN <= ~(((SE  &  SI)  |  (D  &  !SE)));
  end
endmodule

module TBUF_X1 (A, EN, Z);
  input  A, EN;
  output Z;
  assign Z = (EN) ? (A) : 1'bz;
endmodule

module TBUF_X2 (A, EN, Z);
  input  A, EN;
  output Z;
  assign Z = (EN) ? (A) : 1'bz;
endmodule

module TBUF_X4 (A, EN, Z);
  input  A, EN;
  output Z;
  assign Z = (EN) ? (A) : 1'bz;
endmodule

module TBUF_X8 (A, EN, Z);
  input  A, EN;
  output Z;
  assign Z = (EN) ? (A) : 1'bz;
endmodule

module TBUF_X16 (A, EN, Z);
  input  A, EN;
  output Z;
  assign Z = (EN) ? (A) : 1'bz;
endmodule

module TINV_X1 (EN, I, ZN);
  input  EN, I;
  output ZN;
  assign ZN = (EN) ? (!I) : 1'bz;
endmodule

module TLAT_X1 (D, G, OE, Q);
  input  D, G, OE;
  output Q;
  reg latch_q;
  always @(*) if (G) latch_q = D;
  assign Q = OE ? latch_q : 1'bz;
endmodule

module XNOR2_X1 (A, B, ZN);
  input  A, B;
  output ZN;
  assign ZN = (!(A ^ B));
endmodule

module XNOR2_X2 (A, B, ZN);
  input  A, B;
  output ZN;
  assign ZN = (!(A ^ B));
endmodule

module XOR2_X1 (A, B, Z);
  input  A, B;
  output Z;
  assign Z = ((A ^ B));
endmodule

module XOR2_X2 (A, B, Z);
  input  A, B;
  output Z;
  assign Z = ((A ^ B));
endmodule
