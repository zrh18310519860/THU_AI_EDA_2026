module rename_unit (
    input logic clk,
    input logic rst_n,
    input logic uop_valid,
    output logic uop_ready,
    input logic [31:0] uop_pc,
    input logic [31:0] uop_insn,
    input logic [4:0] uop_rs1,
    input logic [4:0] uop_rs2,
    input logic [4:0] uop_rd,
    input logic [31:0] uop_imm,
    input logic [2:0] uop_funct3,
    input logic [4:0] uop_alu_op,
    input logic [15:0] uop_ctrl,
    input logic [31:0] uop_pred_next_pc,
    input logic uop_pred_valid,
    input logic [1:0] uop_pred_type,
    input logic disp_rob_ready,
    input logic [3:0] rob_alloc_id,
    input logic disp_iq_ready,
    output logic disp_valid,
    output logic disp_iq_valid,
    output logic [31:0] disp_pc,
    output logic [31:0] disp_insn,
    output logic [15:0] disp_ctrl,
    output logic [2:0] disp_funct3,
    output logic [4:0] disp_alu_op,
    output logic [31:0] disp_imm,
    output logic [4:0] disp_rs1,
    output logic [4:0] disp_rs2,
    output logic [4:0] disp_rd,
    output logic [5:0] disp_prs1,
    output logic [5:0] disp_prs2,
    output logic [5:0] disp_prd,
    output logic [5:0] disp_old_prd,
    output logic [3:0] disp_rob_id,
    output logic [31:0] disp_pred_next_pc,
    output logic disp_pred_valid,
    output logic [1:0] disp_pred_type,
    input logic complete_valid,
    input logic [5:0] complete_prd,
    input logic [31:0] complete_data,
    input logic complete_we,
    input logic commit_valid,
    input logic [5:0] commit_prd,
    input logic [5:0] commit_old_prd,
    input logic [4:0] commit_rd,
    input logic commit_rd_we,
    input logic squash_valid,
    input logic [3:0] squash_rob_id,
    input logic squash_restore,
    output logic ecall_pending,
    output logic [39:0] prf_ready,
    input logic prf_rd_en0,
    input logic [5:0] prf_rd_addr0,
    output logic [31:0] prf_rdata0,
    input logic prf_rd_en1,
    input logic [5:0] prf_rd_addr1,
    output logic [31:0] prf_rdata1,
    input logic [4:0] dbg_reg_addr,
    output logic [31:0] dbg_reg_data,
    output logic [31:0] arch_x10,
    input logic [3:0] rob_head_id,
    input logic [3:0] commit_rob_id
);

    reg [31:0] prf [0:39];
    reg [39:0] ready;
    reg [39:0] free_bits;
    reg [5:0] free_head;
    reg [5:0] spec_rat [0:31];
    reg [5:0] committed_rat [0:31];
    reg [191:0] ckpt_rat_bank [0:7];
    reg [5:0] ckpt_head [0:7];
    reg [7:0] ckpt_valid;
    reg [5:0] alloc_pr [0:7];
    reg [7:0] alloc_valid;

    reg [39:0] free_for_alloc;
    reg [39:0] mask_hi;
    reg [39:0] mask_lo;
    reg [39:0] scan_hi;
    reg [39:0] scan_lo;
    reg found_hi;
    reg found_lo;
    reg alloc_found;
    reg [5:0] alloc_sel;
    reg [5:0] alloc_next_head;

    integer alloc_i;
    integer rst_i;
    integer ck_i;
    integer sq_i;

    reg is_ecall_w;
    reg need_alloc_w;

    always @* begin
        free_for_alloc = free_bits;
        if (commit_valid && commit_rd_we && (commit_old_prd != 6'd0) && (commit_old_prd < 6'd40)) begin
            free_for_alloc[commit_old_prd] = 1'b1;
        end

        mask_hi = 40'hFFFFFFFFFF;
        for (alloc_i = 0; alloc_i < 40; alloc_i = alloc_i + 1) begin
            if (alloc_i < free_head) begin
                mask_hi[alloc_i] = 1'b0;
            end
        end
        mask_lo = ~mask_hi;

        scan_hi = free_for_alloc & mask_hi;
        scan_lo = free_for_alloc & mask_lo;

        found_hi = 1'b0;
        found_lo = 1'b0;
        alloc_sel = 6'd0;
        for (alloc_i = 39; alloc_i >= 0; alloc_i = alloc_i - 1) begin
            if (scan_hi[alloc_i]) begin
                found_hi = 1'b1;
                alloc_sel = alloc_i[5:0];
            end
        end
        if (!found_hi) begin
            for (alloc_i = 39; alloc_i >= 0; alloc_i = alloc_i - 1) begin
                if (scan_lo[alloc_i]) begin
                    found_lo = 1'b1;
                    alloc_sel = alloc_i[5:0];
                end
            end
        end

        alloc_found = found_hi || found_lo;

        if (alloc_sel == 6'd39) begin
            alloc_next_head = 6'd0;
        end else begin
            alloc_next_head = alloc_sel + 6'd1;
        end
    end

    always @* begin
        is_ecall_w = uop_ctrl[8] || (uop_insn == 32'h0000_0073);
        need_alloc_w = uop_ctrl[2] && (uop_rd != 5'd0);

        uop_ready = (!ecall_pending) && (!squash_valid) && disp_rob_ready &&
                    (is_ecall_w ? 1'b1 : disp_iq_ready) &&
                    ((!need_alloc_w) || alloc_found);

        disp_valid = uop_valid && uop_ready;
        disp_iq_valid = disp_valid && (!is_ecall_w);

        disp_pc = uop_pc;
        disp_insn = uop_insn;
        disp_ctrl = uop_ctrl;
        disp_funct3 = uop_funct3;
        disp_alu_op = uop_alu_op;
        disp_imm = uop_imm;
        disp_rs1 = uop_rs1;
        disp_rs2 = uop_rs2;
        disp_rd = uop_rd;

        disp_prs1 = spec_rat[uop_rs1];
        disp_prs2 = spec_rat[uop_rs2];

        if (need_alloc_w) begin
            disp_prd = alloc_sel;
            disp_old_prd = spec_rat[uop_rd];
        end else begin
            disp_prd = 6'd0;
            disp_old_prd = 6'd0;
        end

        disp_rob_id = rob_alloc_id;
        disp_pred_next_pc = uop_pred_next_pc;
        disp_pred_valid = uop_pred_valid;
        disp_pred_type = uop_pred_type;
    end

    assign prf_ready = ready;

    assign prf_rdata0 = (prf_rd_en0 && (prf_rd_addr0 < 6'd40)) ? prf[prf_rd_addr0] : 32'd0;
    assign prf_rdata1 = (prf_rd_en1 && (prf_rd_addr1 < 6'd40)) ? prf[prf_rd_addr1] : 32'd0;

    always @* begin
        if (dbg_reg_addr == 5'd0) begin
            dbg_reg_data = 32'd0;
        end else if (committed_rat[dbg_reg_addr] < 6'd40) begin
            dbg_reg_data = prf[committed_rat[dbg_reg_addr]];
        end else begin
            dbg_reg_data = 32'd0;
        end
    end

    genvar prf_g;
    generate
        for (prf_g = 0; prf_g < 40; prf_g = prf_g + 1) begin : g_prf
            always @(posedge clk) begin
                if (!rst_n) begin
                    prf[prf_g] <= 32'd0;
                end else if (complete_valid && complete_we && (complete_prd == prf_g[5:0]) && (prf_g != 0)) begin
                    prf[prf_g] <= complete_data;
                end
            end
        end
    endgenerate

    always @(posedge clk) begin
        if (!rst_n) begin
            ready <= 40'h00FFFFFFFF;
            free_bits <= 40'hFF00000000;
            free_head <= 6'd0;
            ecall_pending <= 1'b0;
            arch_x10 <= 32'd0;
            alloc_valid <= 8'd0;
            ckpt_valid <= 8'd0;

            for (rst_i = 0; rst_i < 32; rst_i = rst_i + 1) begin
                spec_rat[rst_i] <= rst_i;
                committed_rat[rst_i] <= rst_i;
            end

            for (rst_i = 0; rst_i < 8; rst_i = rst_i + 1) begin
                alloc_pr[rst_i] <= 6'd0;
                ckpt_head[rst_i] <= 6'd0;
            end

            for (rst_i = 0; rst_i < 8; rst_i = rst_i + 1) begin
                ckpt_rat_bank[rst_i] <= 192'd0;
            end
        end else begin
            if (complete_valid && complete_we && (complete_prd != 6'd0) && (complete_prd < 6'd40)) begin
                ready[complete_prd] <= 1'b1;
            end

            if (commit_valid) begin
                if (commit_rd_we && (commit_rd != 5'd0)) begin
                    committed_rat[commit_rd] <= commit_prd;
                    if (commit_rd == 5'd10) begin
                        if (complete_valid && complete_we && (complete_prd == commit_prd) && (complete_prd < 6'd40)) begin
                            arch_x10 <= complete_data;
                        end else if (commit_prd < 6'd40) begin
                            arch_x10 <= prf[commit_prd];
                        end else begin
                            arch_x10 <= 32'd0;
                        end
                    end
                end

                if (commit_rd_we && (commit_old_prd != 6'd0) && (commit_old_prd < 6'd40)) begin
                    free_bits[commit_old_prd] <= 1'b1;
                    ready[commit_old_prd] <= 1'b0;
                end

                alloc_valid[commit_rob_id[2:0]] <= 1'b0;
                ckpt_valid[commit_rob_id[2:0]] <= 1'b0;
            end

            if (squash_valid) begin
                if (squash_restore && ckpt_valid[squash_rob_id[2:0]]) begin
                    for (sq_i = 0; sq_i < 32; sq_i = sq_i + 1) begin
                        spec_rat[sq_i] <= ckpt_rat_bank[squash_rob_id[2:0]][(sq_i * 6) +: 6];
                    end
                    free_head <= ckpt_head[squash_rob_id[2:0]];
                end

                for (sq_i = 0; sq_i < 8; sq_i = sq_i + 1) begin
                    if (((sq_i - rob_head_id) & 7) > ((squash_rob_id - rob_head_id) & 7)) begin
                        if (alloc_valid[sq_i]) begin
                            free_bits[alloc_pr[sq_i]] <= 1'b1;
                            ready[alloc_pr[sq_i]] <= 1'b0;
                        end
                        alloc_valid[sq_i] <= 1'b0;
                        ckpt_valid[sq_i] <= 1'b0;
                    end
                end

                ckpt_valid[squash_rob_id[2:0]] <= 1'b0;
                ecall_pending <= 1'b0;
            end

            if (disp_valid) begin
                alloc_valid[rob_alloc_id[2:0]] <= need_alloc_w;

                if (need_alloc_w) begin
                    alloc_pr[rob_alloc_id[2:0]] <= alloc_sel;
                    free_bits[alloc_sel] <= 1'b0;
                    ready[alloc_sel] <= 1'b0;
                    spec_rat[uop_rd] <= alloc_sel;
                    free_head <= alloc_next_head;
                end

                if (uop_ctrl[5] || uop_ctrl[6] || uop_ctrl[7]) begin
                    ckpt_valid[rob_alloc_id[2:0]] <= 1'b1;
                    if (need_alloc_w) begin
                        ckpt_head[rob_alloc_id[2:0]] <= alloc_next_head;
                    end else begin
                        ckpt_head[rob_alloc_id[2:0]] <= free_head;
                    end

                    for (ck_i = 0; ck_i < 32; ck_i = ck_i + 1) begin
                        if (need_alloc_w && (ck_i == uop_rd)) begin
                            ckpt_rat_bank[rob_alloc_id[2:0]][(ck_i * 6) +: 6] <= alloc_sel;
                        end else begin
                            ckpt_rat_bank[rob_alloc_id[2:0]][(ck_i * 6) +: 6] <= spec_rat[ck_i];
                        end
                    end
                end else begin
                    ckpt_valid[rob_alloc_id[2:0]] <= 1'b0;
                end

                if (is_ecall_w) begin
                    ecall_pending <= 1'b1;
                end
            end
        end
    end

endmodule
