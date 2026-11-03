module issue_queue (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        halted,
    input  logic        disp_iq_valid,
    output logic        disp_iq_ready,
    input  logic [31:0] disp_pc,
    input  logic [31:0] disp_insn,
    input  logic [15:0] disp_ctrl,
    input  logic [2:0]  disp_funct3,
    input  logic [4:0]  disp_alu_op,
    input  logic [31:0] disp_imm,
    input  logic [4:0]  disp_rs1,
    input  logic [4:0]  disp_rs2,
    input  logic [5:0]  disp_prs1,
    input  logic [5:0]  disp_prs2,
    input  logic [5:0]  disp_prd,
    input  logic [3:0]  disp_rob_id,
    input  logic [31:0] disp_pred_next_pc,
    input  logic        disp_pred_valid,
    input  logic [1:0]  disp_pred_type,
    input  logic [39:0] prf_ready,
    output logic        prf_rd_en0,
    output logic [5:0]  prf_rd_addr0,
    input  logic [31:0] prf_rdata0,
    output logic        prf_rd_en1,
    output logic [5:0]  prf_rd_addr1,
    input  logic [31:0] prf_rdata1,
    input  logic [3:0]  rob_head_id,
    input  logic        squash_valid,
    input  logic [3:0]  squash_rob_id,
    output logic        issue_valid,
    input  logic        issue_ready,
    output logic [31:0] issue_pc,
    output logic [31:0] issue_insn,
    output logic [15:0] issue_ctrl,
    output logic [2:0]  issue_funct3,
    output logic [4:0]  issue_alu_op,
    output logic [31:0] issue_imm,
    output logic [31:0] issue_src1,
    output logic [31:0] issue_src2,
    output logic [5:0]  issue_prd,
    output logic [3:0]  issue_rob_id,
    output logic [31:0] issue_pred_next_pc,
    output logic        issue_pred_valid,
    output logic [1:0]  issue_pred_type,
    output logic        issue_is_mem
);

    logic [7:0]  vld;
    logic [31:0] e_pc      [0:7];
    logic [31:0] e_insn    [0:7];
    logic [15:0] e_ctrl    [0:7];
    logic [2:0]  e_funct3  [0:7];
    logic [4:0]  e_alu_op  [0:7];
    logic [31:0] e_imm     [0:7];
    logic [4:0]  e_rs1     [0:7];
    logic [4:0]  e_rs2     [0:7];
    logic [5:0]  e_prs1    [0:7];
    logic [5:0]  e_prs2    [0:7];
    logic [5:0]  e_prd     [0:7];
    logic [3:0]  e_rob     [0:7];
    logic [31:0] e_pred_pc [0:7];
    logic        e_pred_v  [0:7];
    logic [1:0]  e_pred_t  [0:7];

    logic        sel_vld;
    logic [2:0]  sel_idx;
    logic [3:0]  best_age;
    logic [3:0]  cur_age;
    logic        s1_ok;
    logic        s2_ok;
    logic        cur_ok;
    logic        is_mem_e;
    logic        is_ctl_e;

    logic        free_found;
    logic [2:0]  free_idx;
    logic        full;
    logic        issue_acc;

    integer      si;
    integer      fi;
    integer      ci;
    integer      ri;

    always @* begin
        sel_vld  = 1'b0;
        sel_idx  = 3'd0;
        best_age = 4'd8;
        for (si = 0; si < 8; si = si + 1) begin
            cur_age  = (e_rob[si] - rob_head_id) & 3'd7;
            is_mem_e = e_ctrl[si][3] | e_ctrl[si][4];
            is_ctl_e = e_ctrl[si][13] | e_ctrl[si][5] | e_ctrl[si][6] | e_ctrl[si][7];
            s1_ok = 1'b1;
            s2_ok = 1'b1;
            if (e_ctrl[si][0]) begin
                s1_ok = 1'b0;
                for (ri = 0; ri < 40; ri = ri + 1) begin
                    if ((e_prs1[si] == ri[5:0]) && prf_ready[ri])
                        s1_ok = 1'b1;
                end
            end
            if (e_ctrl[si][1]) begin
                s2_ok = 1'b0;
                for (ri = 0; ri < 40; ri = ri + 1) begin
                    if ((e_prs2[si] == ri[5:0]) && prf_ready[ri])
                        s2_ok = 1'b1;
                end
            end
            cur_ok = vld[si] & s1_ok & s2_ok;
            if (is_mem_e | is_ctl_e)
                cur_ok = cur_ok & (e_rob[si] == rob_head_id);
            if (cur_ok && (cur_age < best_age)) begin
                best_age = cur_age;
                sel_idx  = si[2:0];
                sel_vld  = 1'b1;
            end
        end
    end

    assign full      = &vld;
    assign issue_valid = sel_vld && !halted;
    assign issue_acc   = issue_valid && issue_ready;
    assign disp_iq_ready = !halted && !squash_valid && (!full || issue_acc);

    always @* begin
        free_found = 1'b0;
        free_idx   = 3'd0;
        for (fi = 0; fi < 8; fi = fi + 1) begin
            if (!free_found) begin
                if (!vld[fi] || (issue_acc && (sel_idx == fi[2:0]))) begin
                    free_found = 1'b1;
                    free_idx   = fi[2:0];
                end
            end
        end
    end

    assign prf_rd_en0   = issue_valid;
    assign prf_rd_addr0 = issue_valid ? e_prs1[sel_idx] : 6'd0;
    assign prf_rd_en1   = issue_valid;
    assign prf_rd_addr1 = issue_valid ? e_prs2[sel_idx] : 6'd0;

    assign issue_pc          = issue_valid ? e_pc[sel_idx]      : 32'd0;
    assign issue_insn        = issue_valid ? e_insn[sel_idx]    : 32'd0;
    assign issue_ctrl        = issue_valid ? e_ctrl[sel_idx]    : 16'd0;
    assign issue_funct3      = issue_valid ? e_funct3[sel_idx]  : 3'd0;
    assign issue_alu_op      = issue_valid ? e_alu_op[sel_idx]  : 5'd0;
    assign issue_imm         = issue_valid ? e_imm[sel_idx]     : 32'd0;
    assign issue_prd         = issue_valid ? e_prd[sel_idx]     : 6'd0;
    assign issue_rob_id      = issue_valid ? e_rob[sel_idx]     : 4'd0;
    assign issue_pred_next_pc = issue_valid ? e_pred_pc[sel_idx] : 32'd0;
    assign issue_pred_valid  = issue_valid ? e_pred_v[sel_idx]  : 1'b0;
    assign issue_pred_type   = issue_valid ? e_pred_t[sel_idx]  : 2'd0;
    assign issue_is_mem      = issue_valid ? (e_ctrl[sel_idx][3] | e_ctrl[sel_idx][4]) : 1'b0;
    assign issue_src1        = prf_rdata0;
    assign issue_src2        = prf_rdata1;

    always @(posedge clk) begin
        if (!rst_n) begin
            vld <= 8'd0;
        end else begin
            for (ci = 0; ci < 8; ci = ci + 1) begin
                if (vld[ci] && squash_valid &&
                    (((e_rob[ci] - rob_head_id) & 3'd7) >
                     ((squash_rob_id - rob_head_id) & 3'd7)))
                    vld[ci] <= 1'b0;
            end
            if (issue_acc)
                vld[sel_idx] <= 1'b0;
            if (disp_iq_valid && disp_iq_ready) begin
                vld[free_idx]        <= 1'b1;
                e_pc[free_idx]       <= disp_pc;
                e_insn[free_idx]     <= disp_insn;
                e_ctrl[free_idx]     <= disp_ctrl;
                e_funct3[free_idx]   <= disp_funct3;
                e_alu_op[free_idx]   <= disp_alu_op;
                e_imm[free_idx]      <= disp_imm;
                e_rs1[free_idx]      <= disp_rs1;
                e_rs2[free_idx]      <= disp_rs2;
                e_prs1[free_idx]     <= disp_prs1;
                e_prs2[free_idx]     <= disp_prs2;
                e_prd[free_idx]      <= disp_prd;
                e_rob[free_idx]      <= disp_rob_id;
                e_pred_pc[free_idx]  <= disp_pred_next_pc;
                e_pred_v[free_idx]   <= disp_pred_valid;
                e_pred_t[free_idx]   <= disp_pred_type;
            end
        end
    end

endmodule
