// Testbench: reads stimulus.txt (one line per step: lplc2_l lplc2_r lc4_l lc4_r as Q16.16 decimal),
// writes rtl_out.txt (v_gf_l v_gf_r v_ng_l v_ng_r mode gf_spike initiated).
`timescale 1ns/1ps
module tb;
    reg clk = 0, rst = 1, step = 0;
    reg [31:0] a, b, c, d;
    wire [31:0] vgl, vgr, vnl, vnr;
    wire [1:0] mode;
    wire spk, ini;
    integer fi, fo, r;
    gf_descending dut(.clk(clk), .rst(rst), .step(step), .lplc2_l(a), .lplc2_r(b), .lc4_l(c), .lc4_r(d),
                      .v_gf_l(vgl), .v_gf_r(vgr), .v_ng_l(vnl), .v_ng_r(vnr), .mode(mode), .gf_spike(spk), .initiated(ini));
    always #5 clk = ~clk;
    initial begin
        fi = $fopen("stimulus.txt", "r");
        fo = $fopen("rtl_out.txt", "w");
        a = 0; b = 0; c = 0; d = 0;
        @(posedge clk); @(posedge clk); rst = 0;
        while (!$feof(fi)) begin
            r = $fscanf(fi, "%d %d %d %d\n", a, b, c, d);
            if (r == 4) begin
                @(negedge clk); step = 1;
                @(posedge clk); #1 step = 0;
                $fwrite(fo, "%0d %0d %0d %0d %0d %0d %0d\n", vgl, vgr, vnl, vnr, mode, spk, ini);
            end
        end
        $fclose(fo);
        $finish;
    end
endmodule
