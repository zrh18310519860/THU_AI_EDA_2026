module reorder_buffer (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        alloc_valid,
    input  logic [31:0] alloc_pc,
    input  logic [31:0] alloc_insn,
    input  logic [4:0]  alloc_rd,
    input  logic [5:0]  alloc_pd,
    input  logic [5:0]  alloc_old_pd,
    input  logic        alloc_has_rd,
    input  logic        alloc_is_branch,
    input  logic        alloc_is_jump,
    input  logic        alloc_is_ecall,
    input  logic        alloc_is_load,
    input  logic        alloc_is_store,
    input  logic [31:0] alloc_pred_next_pc,
    input  logic        alloc_pred_valid,
    input  logic [2:0]  alloc_ckpt_id,
    output logic        alloc_ready,
    output logic [2:0]  alloc_rob_id,
    input  logic        int_complete_valid,
    input  logic [2:0]  int_complete_rob_id,
    input  logic [5:0]  int_complete_pd,
    input  logic [31:0] int_complete_data,
    input  logic [31:0] int_complete_actual_next_pc,
    input  logic        int_complete_taken,
    input  logic        lsu_complete_valid,
    input  logic [2:0]  lsu_complete_rob_id,
    input  logic [5:0]  lsu_complete_pd,
    input  logic [31:0] lsu_complete_data,
    input  logic        lsu_complete_writes_rd,
    input  logic        lsu_complete_is_store_done,
    input  logic        store_accept_valid,
    input  logic [2:0]  store_accept_rob_id,
    input  logic [31:0] commit_x10,
    output logic        commit_valid,
    output logic [4:0]  commit_rd,
    output logic [5:0]  commit_pd,
    output logic        commit_has_rd,
    output logic [5:0]  commit_old_pd,
    output logic        retire_valid,
    output logic [31:0] retire_pc,
    output logic [31:0] retire_insn,
    output logic        head_valid,
    output logic [2:0]  head_rob_id,
    output logic        head_is_load,
    output logic        head_is_store,
    output logic        head_complete,
    output logic        resolve_valid,
    output logic        resolve_mispredict,
    output logic        resolve_taken,
    output logic [31:0] resolve_pc,
    output logic [31:0] resolve_next_pc,
    output logic        resolve_is_branch,
    output logic        resolve_is_jump,
    output logic        squash_valid,
    output logic [2:0]  squash_rob_head,
    output logic [2:0]  squash_rob_tail,
    output logic [2:0]  squash_ckpt_id,
    output logic [31:0] squash_pc,
    output logic        halt_req,
    output logic [31:0] halt_exit_code
);

    // ROB entry storage
    logic [31:0] e_pc       [0:7];
    logic [31:0] e_insn     [0:7];
    logic [4:0]  e_rd       [0:7];
    logic [5:0]  e_pd       [0:7];
    logic [5:0]  e_old_pd   [0:7];
    logic        e_has_rd   [0:7];
    logic        e_is_branch[0:7];
    logic        e_is_jump  [0:7];
    logic        e_is_ecall [0:7];
    logic        e_is_load  [0:7];
    logic        e_is_store [0:7];
    logic [31:0] e_pred_npc [0:7];
    logic        e_pred_vld [0:7];
    logic [2:0]  e_ckpt_id  [0:7];
    logic        e_valid    [0:7];
    logic        e_complete [0:7];
    logic        e_store_done[0:7];
    logic [31:0] e_data     [0:7];
    logic [31:0] e_actual_npc[0:7];
    logic        e_taken    [0:7];

    logic [2:0] head_ptr;
    logic [2:0] tail_ptr;
    logic [3:0] count;

    // ---- combinational head view ----
    logic        head_v;
    logic        head_c;
    logic        head_is_ecall_c;
    logic        head_is_store_c;
    logic        head_is_load_c;
    logic        head_is_branch_c;
    logic        head_is_jump_c;
    logic        head_store_done_c;
    logic [31:0] head_pc_c;
    logic [31:0] head_insn_c;
    logic [31:0] head_pred_npc_c;
    logic        head_pred_vld_c;
    logic [31:0] head_actual_npc_c;
    logic        head_taken_c;
    logic [2:0]  head_ckpt_c;

    assign head_v            = e_valid[head_ptr];
    assign head_c            = e_complete[head_ptr];
    assign head_is_ecall_c   = e_is_ecall[head_ptr];
    assign head_is_store_c   = e_is_store[head_ptr];
    assign head_is_load_c    = e_is_load[head_ptr];
    assign head_is_branch_c  = e_is_branch[head_ptr];
    assign head_is_jump_c    = e_is_jump[head_ptr];
    assign head_store_done_c = e_store_done[head_ptr];
    assign head_pc_c         = e_pc[head_ptr];
    assign head_insn_c       = e_insn[head_ptr];
    assign head_pred_npc_c   = e_pred_npc[head_ptr];
    assign head_pred_vld_c   = e_pred_vld[head_ptr];
    assign head_actual_npc_c = e_actual_npc[head_ptr];
    assign head_taken_c      = e_taken[head_ptr];
    assign head_ckpt_c       = e_ckpt_id[head_ptr];

    // store acceptance for head this cycle
    logic store_ok;
    assign store_ok = store_accept_valid && (store_accept_rob_id == head_ptr);

    // retire condition
    logic retire_c;
    logic is_control;
    assign is_control = head_is_branch_c || head_is_jump_c;
    assign retire_c = head_v && head_c &&
                      (!head_is_store_c || head_store_done_c || store_ok) &&
                      (!head_is_ecall_c || 1'b1);

    // resolve (control at head, complete)
    logic resolve_c;
    assign resolve_c = head_v && head_c && is_control;

    logic mispredict_c;
    assign mispredict_c = resolve_c && (head_actual_npc_c != head_pred_npc_c);

    // ---- outputs ----
    assign alloc_ready = (count != 4'd8);
    assign alloc_rob_id = tail_ptr;

    assign commit_valid  = retire_c && e_has_rd[head_ptr];
    assign commit_rd     = e_rd[head_ptr];
    assign commit_pd     = e_pd[head_ptr];
    assign commit_has_rd = e_has_rd[head_ptr];
    assign commit_old_pd = e_old_pd[head_ptr];

    assign retire_valid = retire_c;
    assign retire_pc    = head_pc_c;
    assign retire_insn  = head_insn_c;

    assign head_valid    = head_v;
    assign head_rob_id   = head_ptr;
    assign head_is_load  = head_is_load_c;
    assign head_is_store = head_is_store_c;
    assign head_complete = head_c;

    assign resolve_valid      = resolve_c;
    assign resolve_mispredict = mispredict_c;
    assign resolve_taken      = head_taken_c;
    assign resolve_pc         = head_pc_c;
    assign resolve_next_pc    = head_actual_npc_c;
    assign resolve_is_branch  = head_is_branch_c;
    assign resolve_is_jump    = head_is_jump_c;

    assign squash_valid    = mispredict_c;
    assign squash_rob_head = head_ptr;
    assign squash_rob_tail = tail_ptr;
    assign squash_ckpt_id  = head_ckpt_c;
    assign squash_pc       = head_actual_npc_c;

    assign halt_req       = retire_c && head_is_ecall_c;
    assign halt_exit_code = commit_x10;

    // ---- sequential ----
    integer i;
    always @(posedge clk) begin
        if (!rst_n) begin
            head_ptr <= 3'd0;
            tail_ptr <= 3'd0;
            count    <= 4'd0;
            for (i = 0; i < 8; i = i + 1) begin
                e_valid[i]      <= 1'b0;
                e_complete[i]   <= 1'b0;
                e_store_done[i] <= 1'b0;
                e_pc[i]         <= 32'd0;
                e_insn[i]       <= 32'd0;
                e_rd[i]         <= 5'd0;
                e_pd[i]         <= 6'd0;
                e_old_pd[i]     <= 6'd0;
                e_has_rd[i]     <= 1'b0;
                e_is_branch[i]  <= 1'b0;
                e_is_jump[i]    <= 1'b0;
                e_is_ecall[i]   <= 1'b0;
                e_is_load[i]    <= 1'b0;
                e_is_store[i]   <= 1'b0;
                e_pred_npc[i]   <= 32'd0;
                e_pred_vld[i]   <= 1'b0;
                e_ckpt_id[i]    <= 3'd0;
                e_data[i]       <= 32'd0;
                e_actual_npc[i] <= 32'd0;
                e_taken[i]      <= 1'b0;
            end
        end else begin
            // squash: clear entries younger than head (all except head itself)
            if (mispredict_c) begin
                for (i = 0; i < 8; i = i + 1) begin
                    if (i != head_ptr) begin
                        e_valid[i]      <= 1'b0;
                        e_complete[i]   <= 1'b0;
                        e_store_done[i] <= 1'b0;
                    end
                end
                if (retire_c) begin
                    // resolving control retires this same edge: drop it too
                    e_valid[head_ptr]      <= 1'b0;
                    e_complete[head_ptr]   <= 1'b0;
                    e_store_done[head_ptr] <= 1'b0;
                    head_ptr <= head_ptr + 3'd1;
                    tail_ptr <= (head_ptr + 3'd1);
                    count    <= 4'd0;
                end else begin
                    tail_ptr <= (head_ptr + 3'd1);
                    count    <= 4'd1;
                end
            end

            // completion updates (only if not squashed this cycle)
            if (!mispredict_c) begin
                if (int_complete_valid && e_valid[int_complete_rob_id]) begin
                    e_complete[int_complete_rob_id]   <= 1'b1;
                    e_data[int_complete_rob_id]       <= int_complete_data;
                    e_actual_npc[int_complete_rob_id] <= int_complete_actual_next_pc;
                    e_taken[int_complete_rob_id]      <= int_complete_taken;
                end
                if (lsu_complete_valid && e_valid[lsu_complete_rob_id]) begin
                    e_complete[lsu_complete_rob_id] <= 1'b1;
                    e_data[lsu_complete_rob_id]     <= lsu_complete_data;
                    if (lsu_complete_is_store_done)
                        e_store_done[lsu_complete_rob_id] <= 1'b1;
                end
                if (store_accept_valid && e_valid[store_accept_rob_id]) begin
                    e_store_done[store_accept_rob_id] <= 1'b1;
                end
            end

            // retire (head advances)
            if (retire_c && !mispredict_c) begin
                e_valid[head_ptr]      <= 1'b0;
                e_complete[head_ptr]   <= 1'b0;
                e_store_done[head_ptr] <= 1'b0;
                head_ptr <= head_ptr + 3'd1;
                count <= count - 4'd1;
            end

            // allocate (only if not squashed this cycle)
            if (!mispredict_c && alloc_valid && alloc_ready) begin
                e_valid[tail_ptr]      <= 1'b1;
                e_complete[tail_ptr]   <= 1'b0;
                e_store_done[tail_ptr] <= 1'b0;
                e_pc[tail_ptr]         <= alloc_pc;
                e_insn[tail_ptr]       <= alloc_insn;
                e_rd[tail_ptr]         <= alloc_rd;
                e_pd[tail_ptr]         <= alloc_pd;
                e_old_pd[tail_ptr]     <= alloc_old_pd;
                e_has_rd[tail_ptr]     <= alloc_has_rd;
                e_is_branch[tail_ptr]  <= alloc_is_branch;
                e_is_jump[tail_ptr]    <= alloc_is_jump;
                e_is_ecall[tail_ptr]   <= alloc_is_ecall;
                e_is_load[tail_ptr]    <= alloc_is_load;
                e_is_store[tail_ptr]   <= alloc_is_store;
                e_pred_npc[tail_ptr]   <= alloc_pred_next_pc;
                e_pred_vld[tail_ptr]   <= alloc_pred_valid;
                e_ckpt_id[tail_ptr]    <= alloc_ckpt_id;
                e_data[tail_ptr]       <= 32'd0;
                e_actual_npc[tail_ptr] <= 32'd0;
                e_taken[tail_ptr]      <= 1'b0;
                tail_ptr <= tail_ptr + 3'd1;
                if (!retire_c)
                    count <= count + 4'd1;
                else
                    count <= count;
            end
        end
    end

endmodule
