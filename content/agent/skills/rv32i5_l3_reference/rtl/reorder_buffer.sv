module reorder_buffer (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        halted,
    input  logic        disp_valid,
    output logic        disp_rob_ready,
    output logic [3:0]  rob_alloc_id,
    input  logic [31:0] disp_pc,
    input  logic [31:0] disp_insn,
    input  logic [15:0] disp_ctrl,
    input  logic [4:0]  disp_rd,
    input  logic [5:0]  disp_prd,
    input  logic [5:0]  disp_old_prd,
    input  logic [31:0] disp_pred_next_pc,
    input  logic        disp_pred_valid,
    input  logic [1:0]  disp_pred_type,
    input  logic        complete_valid,
    input  logic [3:0]  complete_rob_id,
    input  logic        squash_valid,
    input  logic [3:0]  squash_rob_id,
    output logic        commit_valid,
    output logic [3:0]  commit_rob_id,
    output logic [31:0] commit_pc,
    output logic [31:0] commit_insn,
    output logic [4:0]  commit_rd,
    output logic [5:0]  commit_prd,
    output logic [5:0]  commit_old_prd,
    output logic        commit_rd_we,
    output logic        commit_is_store,
    output logic        commit_is_ecall,
    output logic [3:0]  rob_head_id,
    output logic        rob_full,
    output logic        rob_empty,
    output logic        head_complete,
    output logic        head_is_store,
    output logic        head_is_ecall,
    output logic        ecall_pending,
    output logic        retire_valid,
    output logic [31:0] retire_pc,
    output logic [31:0] retire_insn
);

    logic [31:0] pc_q      [0:7];
    logic [31:0] insn_q    [0:7];
    logic [15:0] ctrl_q    [0:7];
    logic [4:0]  rd_q      [0:7];
    logic [5:0]  prd_q     [0:7];
    logic [5:0]  old_prd_q [0:7];
    logic        valid_q   [0:7];
    logic        done_q    [0:7];

    logic [2:0]  head_q;
    logic [2:0]  tail_q;
    logic [3:0]  count_q;
    logic        ecall_pending_q;
    logic [2:0]  ecall_id_q;

    logic        alloc_e;
    logic        ecall_commit;
    logic [2:0]  squash_off;
    logic [3:0]  o_min_c;
    logic [3:0]  surv_c;
    logic [3:0]  cnt_c;
    logic [2:0]  head_c;
    logic [2:0]  tail_c;

    integer i;
    integer j;
    integer k;

    assign rob_empty    = (count_q == 4'd0);
    assign rob_full     = (count_q == 4'd8);
    assign rob_head_id  = {1'b0, head_q};
    assign rob_alloc_id = {1'b0, tail_q};

    assign head_complete = valid_q[head_q] && done_q[head_q];
    assign head_is_store = valid_q[head_q] && ctrl_q[head_q][4];
    assign head_is_ecall = valid_q[head_q] && ctrl_q[head_q][8];

    assign commit_valid    = valid_q[head_q] && done_q[head_q] && (!halted);
    assign commit_rob_id   = {1'b0, head_q};
    assign commit_pc       = pc_q[head_q];
    assign commit_insn     = insn_q[head_q];
    assign commit_rd       = rd_q[head_q];
    assign commit_prd      = prd_q[head_q];
    assign commit_old_prd  = old_prd_q[head_q];
    assign commit_rd_we    = ctrl_q[head_q][2] && (rd_q[head_q] != 5'd0);
    assign commit_is_store = ctrl_q[head_q][4];
    assign commit_is_ecall = ctrl_q[head_q][8];

    assign retire_valid = commit_valid;
    assign retire_pc    = commit_pc;
    assign retire_insn  = commit_insn;

    assign ecall_pending = ecall_pending_q;

    assign disp_rob_ready = (!rob_full) && (!halted) && (!squash_valid) && (!ecall_pending_q);
    assign alloc_e        = disp_valid && disp_rob_ready;

    assign ecall_commit = commit_valid && ctrl_q[head_q][8];
    assign squash_off   = squash_rob_id[2:0] - head_q;

    always @* begin
        o_min_c = commit_valid ? 4'd1 : 4'd0;
        surv_c  = 4'd0;
        if (count_q != 4'd0) begin
            if (ecall_commit) begin
                surv_c = 4'd0;
            end else if (squash_valid) begin
                if ({1'b0, squash_off} >= o_min_c)
                    surv_c = {1'b0, squash_off} - o_min_c + 4'd1;
                else
                    surv_c = 4'd0;
            end else begin
                surv_c = count_q - o_min_c;
            end
        end
        cnt_c  = surv_c + (alloc_e ? 4'd1 : 4'd0);
        head_c = head_q + (commit_valid ? 3'd1 : 3'd0);
        tail_c = head_c + cnt_c[2:0];
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            head_q          <= 3'd0;
            tail_q          <= 3'd0;
            count_q         <= 4'd0;
            ecall_pending_q <= 1'b0;
            ecall_id_q      <= 3'd0;
            for (i = 0; i < 8; i = i + 1) begin
                valid_q[i] <= 1'b0;
                done_q[i]  <= 1'b0;
            end
        end else begin
            head_q  <= head_c;
            tail_q  <= tail_c;
            count_q <= cnt_c;

            if (complete_valid && valid_q[complete_rob_id[2:0]])
                done_q[complete_rob_id[2:0]] <= 1'b1;

            if (alloc_e) begin
                valid_q[tail_q]   <= 1'b1;
                done_q[tail_q]    <= disp_ctrl[8];
                pc_q[tail_q]      <= disp_pc;
                insn_q[tail_q]    <= disp_insn;
                ctrl_q[tail_q]    <= disp_ctrl;
                rd_q[tail_q]      <= disp_rd;
                prd_q[tail_q]     <= disp_prd;
                old_prd_q[tail_q] <= disp_old_prd;
            end

            if (commit_valid)
                valid_q[head_q] <= 1'b0;

            if (squash_valid) begin
                for (j = 0; j < 8; j = j + 1) begin
                    if (valid_q[j] && (((j - head_q) & 7) > squash_off))
                        valid_q[j] <= 1'b0;
                end
            end

            if (ecall_commit) begin
                for (k = 0; k < 8; k = k + 1) begin
                    if (((k - head_q) & 7) != 0)
                        valid_q[k] <= 1'b0;
                end
            end

            if (squash_valid && ecall_pending_q && (((ecall_id_q - head_q) & 7) > squash_off)) begin
                ecall_pending_q <= 1'b0;
            end else if (ecall_commit) begin
                ecall_pending_q <= 1'b0;
            end else if (alloc_e && disp_ctrl[8]) begin
                ecall_pending_q <= 1'b1;
                ecall_id_q      <= tail_q;
            end
        end
    end

endmodule
