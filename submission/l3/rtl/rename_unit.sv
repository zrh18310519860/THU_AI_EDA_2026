module rename_unit (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        dec_valid,
    input  logic [31:0] dec_pc,
    input  logic [31:0] dec_insn,
    input  logic [4:0]  dec_rs1,
    input  logic [4:0]  dec_rs2,
    input  logic [4:0]  dec_rd,
    input  logic        dec_uses_rs1,
    input  logic        dec_uses_rs2,
    input  logic        dec_writes_rd,
    input  logic        dec_is_branch,
    input  logic        dec_is_jump,
    input  logic        dec_is_ecall,
    input  logic        dec_is_load,
    input  logic        dec_is_store,
    input  logic [31:0] dec_imm,
    input  logic [2:0]  dec_funct3,
    input  logic [31:0] dec_pred_next_pc,
    input  logic        dec_pred_valid,
    input  logic        rob_alloc_ready,
    input  logic [2:0]  rob_alloc_id,
    input  logic        iq_alloc_ready,
    input  logic        lsq_alloc_ready,
    output logic        dec_ready,
    output logic [2:0]  disp_rob_id,
    output logic [31:0] disp_pc,
    output logic [31:0] disp_insn,
    output logic [4:0]  disp_rs1,
    output logic [4:0]  disp_rs2,
    output logic [4:0]  disp_rd,
    output logic [5:0]  disp_ps1,
    output logic [5:0]  disp_ps2,
    output logic [5:0]  disp_pd,
    output logic        disp_uses_rs1,
    output logic        disp_uses_rs2,
    output logic        disp_writes_rd,
    output logic        disp_is_branch,
    output logic        disp_is_jump,
    output logic        disp_is_ecall,
    output logic        disp_is_load,
    output logic        disp_is_store,
    output logic [31:0] disp_imm,
    output logic [2:0]  disp_funct3,
    output logic [31:0] disp_pred_next_pc,
    output logic        disp_pred_valid,
    output logic [5:0]  disp_old_pd,
    output logic [2:0]  disp_ckpt_id,
    output logic        disp_alloc_ckpt,
    output logic        ecall_barrier,
    input  logic [5:0]  int_prf_raddr1,
    input  logic [5:0]  int_prf_raddr2,
    output logic [31:0] int_prf_rdata1,
    output logic [31:0] int_prf_rdata2,
    input  logic [5:0]  lsu_prf_raddr1,
    input  logic [5:0]  lsu_prf_raddr2,
    output logic [31:0] lsu_prf_rdata1,
    output logic [31:0] lsu_prf_rdata2,
    output logic [39:0] prf_ready_vec,
    input  logic        int_wb_valid,
    input  logic [5:0]  int_wb_pd,
    input  logic [31:0] int_wb_data,
    input  logic        lsu_wb_valid,
    input  logic [5:0]  lsu_wb_pd,
    input  logic [31:0] lsu_wb_data,
    input  logic        commit_valid,
    input  logic [4:0]  commit_rd,
    input  logic [5:0]  commit_pd,
    input  logic        commit_has_rd,
    input  logic [5:0]  commit_old_pd,
    input  logic        squash_valid,
    input  logic [2:0]  squash_ckpt_id,
    input  logic [4:0]  dbg_reg_addr,
    output logic [31:0] dbg_reg_data,
    output logic [31:0] committed_x10
);

    // PRF: 40 x 32
    logic [31:0] prf [0:39];

    // ready vector
    logic [39:0] ready_vec;

    // speculative RAT (arch reg -> PR), committed RAT
    logic [5:0] spec_rat [0:31];
    logic [5:0] comm_rat [0:31];

    // checkpoints: 8 x 192-bit packed RAT snapshots
    logic [191:0] ckpt_rat [0:7];
    logic [7:0]   ckpt_valid;
    logic [2:0]   ckpt_alloc_ptr;

    // free-PR mask: bit i set means PR i is free (PR0..PR31 never free)
    logic [39:0] free_mask;

    // per-checkpoint free-PR mask snapshot for reclaim on squash
    logic [39:0] ckpt_free_mask [0:7];

    // ecall barrier
    logic ecall_barrier_r;

    // ---- combinational decode of dispatch ----
    logic        need_iq;
    logic        need_lsu;
    logic        can_alloc;
    logic [5:0]  pd_alloc;
    logic [5:0]  old_pd;
    logic [5:0]  ps1, ps2;
    logic        alloc_ckpt;
    logic [2:0]  ckpt_id;
    logic        free_any;
    logic [5:0]  free_lowest;

    // lowest set bit of free_mask (PR32..PR39)
    always @* begin
        free_any    = 1'b0;
        free_lowest = 6'd0;
        for (integer k = 39; k >= 0; k = k - 1) begin
            if (free_mask[k]) begin
                free_any    = 1'b1;
                free_lowest = k[5:0];
            end
        end
    end

    always @* begin
        need_iq  = !(dec_is_load || dec_is_store || dec_is_ecall);
        need_lsu = dec_is_load || dec_is_store;
        can_alloc = 1'b1;
        if (dec_writes_rd && dec_rd != 5'd0) begin
            if (!free_any)
                can_alloc = 1'b0;
        end
    end

    assign dec_ready = dec_valid && !ecall_barrier_r && !squash_valid &&
                       rob_alloc_ready &&
                       (!need_iq || iq_alloc_ready) &&
                       (!need_lsu || lsq_alloc_ready) &&
                       can_alloc;

    // source physical regs from speculative RAT
    assign ps1 = (dec_rs1 == 5'd0) ? 6'd0 : spec_rat[dec_rs1];
    assign ps2 = (dec_rs2 == 5'd0) ? 6'd0 : spec_rat[dec_rs2];

    // destination
    assign old_pd = (dec_rd == 5'd0) ? 6'd0 : spec_rat[dec_rd];
    assign pd_alloc = (dec_writes_rd && dec_rd != 5'd0) ? free_lowest : 6'd0;

    assign alloc_ckpt = dec_is_branch || dec_is_jump;
    assign ckpt_id = ckpt_alloc_ptr;

    // dispatch outputs
    assign disp_rob_id       = rob_alloc_id;
    assign disp_pc           = dec_pc;
    assign disp_insn         = dec_insn;
    assign disp_rs1          = dec_rs1;
    assign disp_rs2          = dec_rs2;
    assign disp_rd           = dec_rd;
    assign disp_ps1          = ps1;
    assign disp_ps2          = ps2;
    assign disp_pd           = pd_alloc;
    assign disp_uses_rs1     = dec_uses_rs1;
    assign disp_uses_rs2     = dec_uses_rs2;
    assign disp_writes_rd    = dec_writes_rd && (dec_rd != 5'd0);
    assign disp_is_branch    = dec_is_branch;
    assign disp_is_jump      = dec_is_jump;
    assign disp_is_ecall     = dec_is_ecall;
    assign disp_is_load      = dec_is_load;
    assign disp_is_store     = dec_is_store;
    assign disp_imm          = dec_imm;
    assign disp_funct3       = dec_funct3;
    assign disp_pred_next_pc = dec_pred_next_pc;
    assign disp_pred_valid   = dec_pred_valid;
    assign disp_old_pd       = old_pd;
    assign disp_ckpt_id      = ckpt_id;
    assign disp_alloc_ckpt   = alloc_ckpt;

    assign ecall_barrier = ecall_barrier_r;

    // PRF read ports (addresses supplied by IQ / LSU)
    assign int_prf_rdata1 = (int_prf_raddr1 == 6'd0) ? 32'd0 : prf[int_prf_raddr1];
    assign int_prf_rdata2 = (int_prf_raddr2 == 6'd0) ? 32'd0 : prf[int_prf_raddr2];
    assign lsu_prf_rdata1 = (lsu_prf_raddr1 == 6'd0) ? 32'd0 : prf[lsu_prf_raddr1];
    assign lsu_prf_rdata2 = (lsu_prf_raddr2 == 6'd0) ? 32'd0 : prf[lsu_prf_raddr2];

    assign prf_ready_vec = ready_vec;

    // debug read from committed RAT
    logic [5:0] dbg_pd;
    always @* begin
        if (dbg_reg_addr == 5'd0)
            dbg_pd = 6'd0;
        else
            dbg_pd = comm_rat[dbg_reg_addr];
    end
    assign dbg_reg_data = (dbg_pd == 6'd0) ? 32'd0 : prf[dbg_pd];

    logic [5:0] comm_x10_pd;
    assign comm_x10_pd = comm_rat[5'd10];
    assign committed_x10 = (comm_x10_pd == 6'd0) ? 32'd0 : prf[comm_x10_pd];

    // ---- sequential ----
    integer i;

    always @(posedge clk) begin
        if (!rst_n) begin
            for (i = 0; i < 40; i = i + 1)
                prf[i] <= 32'd0;
            ready_vec <= 40'h0000_FFFF_FFFF;
            for (i = 0; i < 32; i = i + 1) begin
                spec_rat[i] <= i[5:0];
                comm_rat[i] <= i[5:0];
            end
            for (i = 0; i < 8; i = i + 1) begin
                ckpt_rat[i] <= 192'd0;
                ckpt_valid[i] <= 1'b0;
                ckpt_free_mask[i] <= 40'd0;
            end
            ckpt_alloc_ptr <= 3'd0;
            free_mask <= 40'hFF_0000_0000;
            ecall_barrier_r <= 1'b0;
        end else begin
            // Priority: squash > commit reclaim > wb write > dispatch alloc
            if (squash_valid) begin
                // restore RAT from checkpoint
                for (i = 0; i < 32; i = i + 1)
                    spec_rat[i] <= ckpt_rat[squash_ckpt_id][i*6 +: 6];
                // The resolving control instruction itself is older than the
                // squash point and commits on this same edge. Reapply its
                // rd->pd mapping so its link/result is not lost.
                if (commit_valid && commit_has_rd && commit_rd != 5'd0) begin
                    spec_rat[commit_rd] <= commit_pd;
                    comm_rat[commit_rd] <= commit_pd;
                end
                // Restore free-PR mask from the checkpoint snapshot, then
                // reserve the resolving commit's pd (allocated after the
                // checkpoint) and free its real old_pd reclaim.
                begin : squash_free_rebuild
                    logic [39:0] fm_tmp;
                    fm_tmp = ckpt_free_mask[squash_ckpt_id];
                    if (commit_valid && commit_has_rd && commit_rd != 5'd0) begin
                        fm_tmp[commit_pd] = 1'b0;
                        if (commit_old_pd != 6'd0)
                            fm_tmp[commit_old_pd] = 1'b1;
                    end
                    free_mask <= fm_tmp;
                    // clear ready bits for reclaimed younger PRs
                    for (i = 32; i < 40; i = i + 1) begin
                        if (ready_vec[i] === 1'b0) begin
                            if (!ckpt_free_mask[squash_ckpt_id][i] &&
                                !(commit_valid && commit_has_rd &&
                                  commit_rd != 5'd0 && commit_pd == i[5:0]))
                                ready_vec[i] <= 1'b0;
                        end
                    end
                end
                // invalidate checkpoints at and after squash_ckpt_id
                for (i = 0; i < 8; i = i + 1) begin
                    if (i >= squash_ckpt_id)
                        ckpt_valid[i] <= 1'b0;
                end
                ckpt_alloc_ptr <= squash_ckpt_id;
                ecall_barrier_r <= 1'b0;
            end else begin
                // commit reclaim
                if (commit_valid && commit_has_rd && commit_rd != 5'd0) begin
                    comm_rat[commit_rd] <= commit_pd;
                    if (commit_old_pd != 6'd0)
                        free_mask[commit_old_pd] <= 1'b1;
                end

                // wb write
                if (int_wb_valid && int_wb_pd != 6'd0) begin
                    prf[int_wb_pd] <= int_wb_data;
                    ready_vec[int_wb_pd] <= 1'b1;
                end
                if (lsu_wb_valid && lsu_wb_pd != 6'd0) begin
                    prf[lsu_wb_pd] <= lsu_wb_data;
                    ready_vec[lsu_wb_pd] <= 1'b1;
                end

                // dispatch alloc
                if (dec_ready) begin
                    if (dec_writes_rd && dec_rd != 5'd0) begin
                        spec_rat[dec_rd] <= pd_alloc;
                        ready_vec[pd_alloc] <= 1'b0;
                        free_mask[pd_alloc] <= 1'b0;
                    end
                    if (alloc_ckpt) begin
                        // capture packed RAT
                        for (i = 0; i < 32; i = i + 1)
                            ckpt_rat[ckpt_id][i*6 +: 6] <= spec_rat[i];
                        ckpt_valid[ckpt_id] <= 1'b1;
                        // Snapshot free mask including same-cycle older
                        // commit reclaim so it reflects the post-commit state.
                        begin : ckpt_mask_snap
                            logic [39:0] fm_snap;
                            fm_snap = free_mask;
                            if (commit_valid && commit_has_rd && commit_rd != 5'd0 &&
                                commit_old_pd != 6'd0)
                                fm_snap[commit_old_pd] = 1'b1;
                            ckpt_free_mask[ckpt_id] <= fm_snap;
                        end
                        ckpt_alloc_ptr <= ckpt_alloc_ptr + 3'd1;
                    end
                    if (dec_is_ecall)
                        ecall_barrier_r <= 1'b1;
                end

            end
        end
    end

endmodule
