// Giant Fiber / non-GF descending-neuron stage of the GiantFiber escape model, in fixed point.
//
// Mirrors GiantFiberCircuit::step() in crates/giantfiber-core/src/connectome/giant_fiber.rs for a
// fixed evaluation period (one `step` pulse = DT, default 1 ms):
//   v_gf[s]    += A_GF * (W_GF_LPLC2[s]*lplc2[s] + W_GF_LC4[s]*lc4[s] - v_gf[s])
//   v_nongf[s] += A_NG * (W_NG_LPLC2[s]*lplc2[s] + W_NG_LC4[s]*lc4[s] - v_nongf[s])
//   GF spike if any v_gf >= THR_GF (then v_gf <- 0); non-GF crossing on an upward threshold crossing.
//   Modes: NONE -> LONG on a non-GF crossing, NONE -> SHORT on a GF spike,
//          LONG -> SHORT on a GF spike within LONG_WINDOW steps of long-mode onset,
//          back to NONE REFRACTORY steps after the episode started.
// All values are unsigned Q16.16. Weights default to the FlyWire v783 synapse-count fractions
// (e.g. left GF: LPLC2 458 / (458+374) syn); rtl/check_rtl.py checks these defaults against data.rs.
//
// This is a simulation-level RTL prototype (verified with Icarus Verilog against the Rust engine),
// not a synthesised or timing-closed design. The looming estimator (time surface, angular size)
// is not implemented in RTL.
`timescale 1ns/1ps
module gf_descending #(
    parameter [31:0] W_GF_LPLC2_L = 32'd36076, parameter [31:0] W_GF_LC4_L = 32'd29460,
    parameter [31:0] W_GF_LPLC2_R = 32'd38712, parameter [31:0] W_GF_LC4_R = 32'd26824,
    parameter [31:0] W_NG_LPLC2_L = 32'd34615, parameter [31:0] W_NG_LC4_L = 32'd30921,
    parameter [31:0] W_NG_LPLC2_R = 32'd38427, parameter [31:0] W_NG_LC4_R = 32'd27109,
    parameter [31:0] A_GF   = 32'd6554,   // DT / gf_tau    = 1 ms / 10 ms
    parameter [31:0] A_NG   = 32'd2185,   // DT / nongf_tau = 1 ms / 30 ms
    parameter [31:0] THR_GF = 32'd36045,  // 0.55
    parameter [31:0] THR_NG = 32'd19661,  // 0.30
    parameter integer REFRACTORY  = 300,  // steps
    parameter integer LONG_WINDOW = 30    // steps
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        step,        // one evaluation step
    input  wire [31:0] lplc2_l, lplc2_r, lc4_l, lc4_r,   // Q16.16 activities in [0, 1]
    output reg  [31:0] v_gf_l, v_gf_r, v_ng_l, v_ng_r,
    output reg  [1:0]  mode,        // 0 none, 1 long, 2 short
    output reg         gf_spike,
    output reg         initiated
);
    localparam [1:0] M_NONE = 2'd0, M_LONG = 2'd1, M_SHORT = 2'd2;

    reg        first;
    reg [31:0] t, ep_start, ng_onset;

    function [31:0] mulq;  // Q16.16 multiply, truncating
        input [31:0] a, b;
        reg [63:0] p;
        begin p = a * b; mulq = p[47:16]; end
    endfunction

    // leaky integration step: v + a*(i - v), signed difference
    function [31:0] leak;
        input [31:0] v, i, a;
        reg signed [33:0] d;
        reg signed [65:0] p;
        begin
            d = $signed({2'b00, i}) - $signed({2'b00, v});
            p = d * $signed({34'd0, a});
            leak = v + p[47:16];
        end
    endfunction

    wire [31:0] i_gf_l = mulq(W_GF_LPLC2_L, lplc2_l) + mulq(W_GF_LC4_L, lc4_l);
    wire [31:0] i_gf_r = mulq(W_GF_LPLC2_R, lplc2_r) + mulq(W_GF_LC4_R, lc4_r);
    wire [31:0] i_ng_l = mulq(W_NG_LPLC2_L, lplc2_l) + mulq(W_NG_LC4_L, lc4_l);
    wire [31:0] i_ng_r = mulq(W_NG_LPLC2_R, lplc2_r) + mulq(W_NG_LC4_R, lc4_r);

    wire [31:0] n_gf_l = first ? v_gf_l : leak(v_gf_l, i_gf_l, A_GF);
    wire [31:0] n_gf_r = first ? v_gf_r : leak(v_gf_r, i_gf_r, A_GF);
    wire [31:0] n_ng_l = first ? v_ng_l : leak(v_ng_l, i_ng_l, A_NG);
    wire [31:0] n_ng_r = first ? v_ng_r : leak(v_ng_r, i_ng_r, A_NG);

    wire spike = (n_gf_l >= THR_GF) || (n_gf_r >= THR_GF);
    wire cross = ((v_ng_l < THR_NG) && (n_ng_l >= THR_NG)) || ((v_ng_r < THR_NG) && (n_ng_r >= THR_NG));

    // mode after the refractory check
    wire [1:0] m0 = (mode != M_NONE && t >= ep_start + REFRACTORY) ? M_NONE : mode;

    always @(posedge clk) begin
        if (rst) begin
            v_gf_l <= 0; v_gf_r <= 0; v_ng_l <= 0; v_ng_r <= 0;
            mode <= M_NONE; gf_spike <= 0; initiated <= 0;
            first <= 1; t <= 0; ep_start <= 0; ng_onset <= 0;
        end else if (step) begin
            first <= 0;
            t <= t + 1;
            v_ng_l <= n_ng_l; v_ng_r <= n_ng_r;
            v_gf_l <= spike ? 32'd0 : n_gf_l;
            v_gf_r <= spike ? 32'd0 : n_gf_r;
            gf_spike <= spike;
            initiated <= 0;
            mode <= m0;
            case (m0)
                M_NONE: begin
                    if (spike) begin mode <= M_SHORT; initiated <= 1; ep_start <= t; end
                    else if (cross) begin mode <= M_LONG; initiated <= 1; ep_start <= t; ng_onset <= t; end
                end
                M_LONG: if (spike && t <= ng_onset + LONG_WINDOW) begin mode <= M_SHORT; initiated <= 1; end
                default: ;
            endcase
        end
    end
endmodule
