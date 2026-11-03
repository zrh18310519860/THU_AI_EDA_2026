module issue_queue (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        alloc_valid,
    input  logic [2:0]  alloc_rob_id,
    input  logic [31:0] alloc_pc,
    input  logic [31:0] alloc_insn,
    input  logic [5:0]  alloc_ps1,
    input  logic [5:0]  alloc_ps2,
    input  logic [5:0]  alloc_pd,
    input  logic        alloc_uses_rs1,
    input  logic        alloc_uses_rs2,
    input  logic        alloc_writes_rd,
    input  logic        alloc_is_branch,
    input  logic        alloc_is_jump,
    input  logic [31:0] alloc_pred_next_pc,
    input  logic        alloc_pred_valid,
    output logic        alloc_ready,
    input  logic [39:0] prf_ready_vec,
    output logic [5:0]  prf_raddr1,
    output logic [5:0]  prf_raddr2,
    input  logic [31:0] prf_rdata1,
    input  logic [31:0] prf_rdata2,
    output logic        issue_valid,
    input  logic        issue_ready,
    output logic [2:0]  issue_rob_id,
    output logic [31:0] issue_pc,
    output logic [31:0] issue_insn,
    output logic [31:0] issue_rs1_val,
    output logic [31:0] issue_rs2_val,
    output logic [5:0]  issue_pd,
    output logic        issue_writes_rd,
    output logic        issue_is_branch,
    output logic        issue_is_jump,
    output logic [31:0] issue_pred_next_pc,
    output logic        issue_pred_valid,
    input  logic        squash_valid,
    input  logic [2:0]  squash_rob_head,
    input  logic [2:0]  squash_rob_tail
);

    // 8-entry integer issue queue.
    // Entries are stored in a compacted (age-ordered) form: entry 0 is the
    // oldest.  This makes "oldest ready entry" selection trivial and keeps
    // squash-by-ROB-age simple.  On every accepted event we rebuild the
    // compacted array from the surviving entries plus the new allocation.

    logic        ent_valid   [0:7];
    logic [2:0]  ent_rob_id  [0:7];
    logic [31:0] ent_pc      [0:7];
    logic [31:0] ent_insn    [0:7];
    logic [5:0]  ent_ps1     [0:7];
    logic [5:0]  ent_ps2     [0:7];
    logic [5:0]  ent_pd      [0:7];
    logic        ent_uses1   [0:7];
    logic        ent_uses2   [0:7];
    logic        ent_writes  [0:7];
    logic        ent_branch  [0:7];
    logic        ent_jump    [0:7];
    logic [31:0] ent_pred_npc[0:7];
    logic        ent_pred_v  [0:7];

    // ------------------------------------------------------------------
    // Per-entry readiness from the rename ready vector.
    // ------------------------------------------------------------------
    logic [7:0] ent_ready;

    integer i;
    always @* begin
        for (i = 0; i < 8; i = i + 1) begin
            ent_ready[i] = ent_valid[i] &&
                           (!ent_uses1[i] || prf_ready_vec[ent_ps1[i]]) &&
                           (!ent_uses2[i] || prf_ready_vec[ent_ps2[i]]);
        end
    end

    // ------------------------------------------------------------------
    // Oldest ready entry selection (combinational).
    // ------------------------------------------------------------------
    logic       sel_found;
    logic [2:0] sel_idx;

    always @* begin
        sel_found = 1'b0;
        sel_idx   = 3'd0;
        for (i = 7; i >= 0; i = i - 1) begin
            if (ent_ready[i]) begin
                sel_found = 1'b1;
                sel_idx   = i[2:0];
            end
        end
    end

    // ------------------------------------------------------------------
    // Squash classification: entry is younger than squash point when its
    // circular distance from squash_rob_head is >= distance of squash_tail.
    // ------------------------------------------------------------------
    logic [7:0] ent_squash;

    always @* begin
        for (i = 0; i < 8; i = i + 1) begin
            logic [2:0] d_ent;
            logic [2:0] d_tail;
            d_ent  = ent_rob_id[i] - squash_rob_head;
            d_tail = squash_rob_tail - squash_rob_head;
            ent_squash[i] = squash_valid && ent_valid[i] && (d_ent >= d_tail);
        end
    end

    // ------------------------------------------------------------------
    // Issue outputs (combinational from selected entry).
    // ------------------------------------------------------------------
    always @* begin
        issue_valid       = sel_found;
        issue_rob_id      = ent_rob_id[sel_idx];
        issue_pc          = ent_pc[sel_idx];
        issue_insn        = ent_insn[sel_idx];
        issue_rs1_val     = prf_rdata1;
        issue_rs2_val     = prf_rdata2;
        issue_pd          = ent_pd[sel_idx];
        issue_writes_rd   = ent_writes[sel_idx];
        issue_is_branch   = ent_branch[sel_idx];
        issue_is_jump     = ent_jump[sel_idx];
        issue_pred_next_pc= ent_pred_npc[sel_idx];
        issue_pred_valid  = ent_pred_v[sel_idx];
    end

    // PRF read addresses come from the selected entry's source PR ids.
    always @* begin
        prf_raddr1 = ent_ps1[sel_idx];
        prf_raddr2 = ent_ps2[sel_idx];
    end

    // ------------------------------------------------------------------
    // Accept decisions.
    // ------------------------------------------------------------------
    logic issue_accept;
    logic alloc_accept;
    logic [3:0] live_count;

    always @* begin
        live_count = 4'd0;
        for (i = 0; i < 8; i = i + 1) begin
            if (ent_valid[i] && !ent_squash[i])
                live_count = live_count + 4'd1;
        end
    end

    // Issue pop only when the selected entry is not being squashed.
    assign issue_accept = issue_valid && issue_ready && !ent_squash[sel_idx];

    // Alloc accepted only when there is room after squash and issue pop.
    assign alloc_ready  = rst_n && (live_count < 4'd8) &&
                          !(issue_accept && (live_count == 4'd8));
    assign alloc_accept = alloc_valid && alloc_ready;

    // ------------------------------------------------------------------
    // Sequential state update: rebuild compacted array each edge.
    // Priority: rst_n > squash > issue pop > alloc push.
    // ------------------------------------------------------------------
    integer j;
    always @(posedge clk) begin
        if (!rst_n) begin
            for (j = 0; j < 8; j = j + 1) begin
                ent_valid[j]    <= 1'b0;
                ent_rob_id[j]   <= 3'd0;
                ent_pc[j]       <= 32'd0;
                ent_insn[j]     <= 32'd0;
                ent_ps1[j]      <= 6'd0;
                ent_ps2[j]      <= 6'd0;
                ent_pd[j]       <= 6'd0;
                ent_uses1[j]    <= 1'b0;
                ent_uses2[j]    <= 1'b0;
                ent_writes[j]   <= 1'b0;
                ent_branch[j]   <= 1'b0;
                ent_jump[j]     <= 1'b0;
                ent_pred_npc[j] <= 32'd0;
                ent_pred_v[j]   <= 1'b0;
            end
        end else begin
            // Rebuild compacted array from surviving entries.
            // A surviving entry is valid, not squashed, and not the one
            // being issued this cycle.
            begin : rebuild
                logic [3:0] w;
                logic       keep;
                w = 4'd0;
                for (j = 0; j < 8; j = j + 1) begin
                    keep = ent_valid[j] && !ent_squash[j] &&
                           !(issue_accept && (sel_idx == j[2:0]));
                    if (keep) begin
                        ent_valid[w]    <= 1'b1;
                        ent_rob_id[w]   <= ent_rob_id[j];
                        ent_pc[w]       <= ent_pc[j];
                        ent_insn[w]     <= ent_insn[j];
                        ent_ps1[w]      <= ent_ps1[j];
                        ent_ps2[w]      <= ent_ps2[j];
                        ent_pd[w]       <= ent_pd[j];
                        ent_uses1[w]    <= ent_uses1[j];
                        ent_uses2[w]    <= ent_uses2[j];
                        ent_writes[w]   <= ent_writes[j];
                        ent_branch[w]   <= ent_branch[j];
                        ent_jump[w]     <= ent_jump[j];
                        ent_pred_npc[w] <= ent_pred_npc[j];
                        ent_pred_v[w]   <= ent_pred_v[j];
                        w = w + 4'd1;
                    end
                end
                // Append newly allocated entry (youngest).
                if (alloc_accept) begin
                    ent_valid[w]    <= 1'b1;
                    ent_rob_id[w]   <= alloc_rob_id;
                    ent_pc[w]       <= alloc_pc;
                    ent_insn[w]     <= alloc_insn;
                    ent_ps1[w]      <= alloc_ps1;
                    ent_ps2[w]      <= alloc_ps2;
                    ent_pd[w]       <= alloc_pd;
                    ent_uses1[w]    <= alloc_uses_rs1;
                    ent_uses2[w]    <= alloc_uses_rs2;
                    ent_writes[w]   <= alloc_writes_rd;
                    ent_branch[w]   <= alloc_is_branch;
                    ent_jump[w]     <= alloc_is_jump;
                    ent_pred_npc[w] <= alloc_pred_next_pc;
                    ent_pred_v[w]   <= alloc_pred_valid;
                    w = w + 4'd1;
                end
                // Clear any trailing slots that were not written.
                for (j = 0; j < 8; j = j + 1) begin
                    if (j >= w)
                        ent_valid[j] <= 1'b0;
                end
            end
        end
    end

endmodule
