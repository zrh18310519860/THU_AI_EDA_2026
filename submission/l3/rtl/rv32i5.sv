// rv32i5.sv
// L3 top-level: fetch/decode + branch prediction, rename/dispatch, integer
// issue + ALU execute, LSU (with D-cache), ROB in-order commit, I/D cache
// bridges to the external same-cycle-read word bus, and debug/capability
// outputs.
//
// Protocol notes for downstream integration:
//  * External bus is same-cycle-read: a request is accepted on a rising edge
//    where mem_req_valid && mem_req_ready; mem_resp_valid is the gated accept
//    pulse (mem_req_valid && mem_req_ready) and mem_resp_data = mem_rdata
//    sampled on that same edge. No extra registered external response cycle.
//  * imem_addr/dmem_* are held stable while valid && !ready.
//  * Cache-to-CPU responses are separate registered one-cycle pulses.
//  * dbg_features is a constant 3'b111 from time 0.
//  * Capability events are single-cycle pulses, zero during reset and after
//    halted.
//  * ECALL retires at ROB head; halted is set one edge after that retire and
//    exit_code = committed x10.

module rv32i5 (
    input  logic        clk,
    input  logic        rst_n,
    output logic [31:0] imem_addr,
    input  logic [31:0] imem_rdata,
    output logic        imem_valid,
    input  logic        imem_ready,
    output logic        dmem_valid,
    output logic        dmem_we,
    output logic [31:0] dmem_addr,
    output logic [31:0] dmem_wdata,
    output logic [3:0]  dmem_be,
    input  logic [31:0] dmem_rdata,
    input  logic        dmem_ready,
    input  logic [4:0]  dbg_reg_addr,
    output logic [31:0] dbg_reg_data,
    output logic        halted,
    output logic [31:0] exit_code,
    output logic [2:0]  dbg_features,
    output logic        dbg_retire_valid,
    output logic [31:0] dbg_retire_pc,
    output logic [31:0] dbg_retire_insn,
    output logic        dbg_bp_resolve,
    output logic        dbg_bp_mispredict,
    output logic        dbg_ooo_issue,
    output logic        dbg_ooo_bypass
);

    // ------------------------------------------------------------------
    // Capability constant.
    // ------------------------------------------------------------------
    assign dbg_features = 3'b111;

    // ------------------------------------------------------------------
    // Global control state.
    // ------------------------------------------------------------------
    logic        halted_r;
    logic [31:0] exit_code_r;
    logic [1:0]  flush_epoch_r;

    // ------------------------------------------------------------------
    // Fetch frontend <-> I-cache.
    // ------------------------------------------------------------------
    logic        fe_cpu_req_valid;
    logic [31:0] fe_cpu_req_addr;
    logic        ic_cpu_req_ready;
    logic        ic_cpu_resp_valid;
    logic [31:0] ic_cpu_resp_data;

    logic        fe_pred_req_valid;
    logic [31:0] fe_pred_pc;
    logic        bp_pred_hit;
    logic        bp_pred_taken;
    logic [31:0] bp_pred_target;

    logic        fe_dec_valid;
    logic [31:0] fe_dec_pc;
    logic [31:0] fe_dec_insn;
    logic [31:0] fe_dec_pred_next_pc;
    logic        fe_dec_pred_valid;
    logic        fe_dec_ready;

    // ------------------------------------------------------------------
    // Decode unit.
    // ------------------------------------------------------------------
    logic [4:0]  du_rs1;
    logic [4:0]  du_rs2;
    logic [4:0]  du_rd;
    logic        du_uses_rs1;
    logic        du_uses_rs2;
    logic        du_writes_rd;
    logic        du_is_branch;
    logic        du_is_jump;
    logic        du_is_ecall;
    logic        du_is_load;
    logic        du_is_store;
    logic [31:0] du_imm;
    logic [2:0]  du_funct3;

    // ------------------------------------------------------------------
    // Rename unit.
    // ------------------------------------------------------------------
    logic        ru_dec_ready;
    logic [2:0]  ru_disp_rob_id;
    logic [31:0] ru_disp_pc;
    logic [31:0] ru_disp_insn;
    logic [4:0]  ru_disp_rs1;
    logic [4:0]  ru_disp_rs2;
    logic [4:0]  ru_disp_rd;
    logic [5:0]  ru_disp_ps1;
    logic [5:0]  ru_disp_ps2;
    logic [5:0]  ru_disp_pd;
    logic        ru_disp_uses_rs1;
    logic        ru_disp_uses_rs2;
    logic        ru_disp_writes_rd;
    logic        ru_disp_is_branch;
    logic        ru_disp_is_jump;
    logic        ru_disp_is_ecall;
    logic        ru_disp_is_load;
    logic        ru_disp_is_store;
    logic [31:0] ru_disp_imm;
    logic [2:0]  ru_disp_funct3;
    logic [31:0] ru_disp_pred_next_pc;
    logic        ru_disp_pred_valid;
    logic [5:0]  ru_disp_old_pd;
    logic [2:0]  ru_disp_ckpt_id;
    logic        ru_disp_alloc_ckpt;
    logic        ru_ecall_barrier;
    logic [5:0]  ru_int_prf_raddr1;
    logic [5:0]  ru_int_prf_raddr2;
    logic [31:0] ru_int_prf_rdata1;
    logic [31:0] ru_int_prf_rdata2;
    logic [5:0]  ru_lsu_prf_raddr1;
    logic [5:0]  ru_lsu_prf_raddr2;
    logic [31:0] ru_lsu_prf_rdata1;
    logic [31:0] ru_lsu_prf_rdata2;
    logic [39:0] ru_prf_ready_vec;
    logic [31:0] ru_dbg_reg_data;
    logic [31:0] ru_committed_x10;

    // ------------------------------------------------------------------
    // Issue queue.
    // ------------------------------------------------------------------
    logic        iq_alloc_ready;
    logic        iq_issue_valid;
    logic [2:0]  iq_issue_rob_id;
    logic [31:0] iq_issue_pc;
    logic [31:0] iq_issue_insn;
    logic [31:0] iq_issue_rs1_val;
    logic [31:0] iq_issue_rs2_val;
    logic [5:0]  iq_issue_pd;
    logic        iq_issue_writes_rd;
    logic        iq_issue_is_branch;
    logic        iq_issue_is_jump;
    logic [31:0] iq_issue_pred_next_pc;
    logic        iq_issue_pred_valid;
    logic [5:0]  iq_prf_raddr1;
    logic [5:0]  iq_prf_raddr2;

    // ------------------------------------------------------------------
    // Integer execute unit.
    // ------------------------------------------------------------------
    logic        ie_issue_ready;
    logic        ie_complete_valid;
    logic [2:0]  ie_complete_rob_id;
    logic [5:0]  ie_complete_pd;
    logic [31:0] ie_complete_data;
    logic [31:0] ie_complete_actual_next_pc;
    logic        ie_complete_taken;
    logic        ie_wb_valid;
    logic [5:0]  ie_wb_pd;
    logic [31:0] ie_wb_data;

    // ------------------------------------------------------------------
    // Load/store unit <-> D-cache.
    // ------------------------------------------------------------------
    logic        lsu_alloc_ready;
    logic [5:0]  lsu_prf_raddr1;
    logic [5:0]  lsu_prf_raddr2;
    logic        lsu_cpu_req_valid;
    logic        lsu_cpu_req_we;
    logic [31:0] lsu_cpu_req_addr;
    logic [31:0] lsu_cpu_req_wdata;
    logic [3:0]  lsu_cpu_req_be;
    logic        dc_cpu_req_ready;
    logic        dc_cpu_resp_valid;
    logic [31:0] dc_cpu_resp_rdata;
    logic        lsu_complete_valid;
    logic [2:0]  lsu_complete_rob_id;
    logic [5:0]  lsu_complete_pd;
    logic [31:0] lsu_complete_data;
    logic        lsu_complete_writes_rd;
    logic        lsu_complete_is_store_done;
    logic        lsu_store_accept_valid;
    logic [2:0]  lsu_store_accept_rob_id;
    logic        lsu_wb_valid;
    logic [5:0]  lsu_wb_pd;
    logic [31:0] lsu_wb_data;
    logic        lsu_load_waiting;
    logic [2:0]  lsu_load_waiting_rob_id;

    // ------------------------------------------------------------------
    // Reorder buffer.
    // ------------------------------------------------------------------
    logic        rob_alloc_ready;
    logic [2:0]  rob_alloc_id;
    logic        rob_commit_valid;
    logic [4:0]  rob_commit_rd;
    logic [5:0]  rob_commit_pd;
    logic        rob_commit_has_rd;
    logic [5:0]  rob_commit_old_pd;
    logic        rob_retire_valid;
    logic [31:0] rob_retire_pc;
    logic [31:0] rob_retire_insn;
    logic        rob_head_valid;
    logic [2:0]  rob_head_rob_id;
    logic        rob_head_is_load;
    logic        rob_head_is_store;
    logic        rob_head_complete;
    logic        rob_resolve_valid;
    logic        rob_resolve_mispredict;
    logic        rob_resolve_taken;
    logic [31:0] rob_resolve_pc;
    logic [31:0] rob_resolve_next_pc;
    logic        rob_resolve_is_branch;
    logic        rob_resolve_is_jump;
    logic        rob_squash_valid;
    logic [2:0]  rob_squash_rob_head;
    logic [2:0]  rob_squash_rob_tail;
    logic [2:0]  rob_squash_ckpt_id;
    logic [31:0] rob_squash_pc;
    logic        rob_halt_req;
    logic [31:0] rob_halt_exit_code;

    // ------------------------------------------------------------------
    // I-cache external bus.
    // ------------------------------------------------------------------
    logic        ic_mem_req_valid;
    logic [31:0] ic_mem_req_addr;
    logic        ic_mem_resp_valid;
    logic [31:0] ic_mem_resp_data;

    // ------------------------------------------------------------------
    // D-cache external bus.
    // ------------------------------------------------------------------
    logic        dc_mem_req_valid;
    logic        dc_mem_req_we;
    logic [31:0] dc_mem_req_addr;
    logic [31:0] dc_mem_req_wdata;
    logic [3:0]  dc_mem_req_be;
    logic        dc_mem_resp_valid;
    logic [31:0] dc_mem_resp_data;

    // ------------------------------------------------------------------
    // Redirect / stop-fetch control.
    // ------------------------------------------------------------------
    logic        redirect_valid;
    logic [31:0] redirect_pc;
    logic        stop_fetch;

    assign redirect_valid = rob_squash_valid;
    assign redirect_pc    = rob_squash_pc;
    assign stop_fetch     = ru_ecall_barrier | halted_r;

    // ------------------------------------------------------------------
    // External bus bridges (same-cycle-read).
    // ------------------------------------------------------------------
    logic [31:0] imem_addr_hold;
    always @(posedge clk) begin
        if (!rst_n)
            imem_addr_hold <= 32'd0;
        else if (!halted_r)
            imem_addr_hold <= ic_mem_req_addr;
    end

    assign imem_valid = rst_n & ~halted_r & ic_mem_req_valid;
    assign imem_addr  = halted_r ? imem_addr_hold : ic_mem_req_addr;
    assign ic_mem_resp_valid = imem_valid & imem_ready;
    assign ic_mem_resp_data  = imem_rdata;

    assign dmem_valid = rst_n & ~halted_r & dc_mem_req_valid;
    assign dmem_we    = dc_mem_req_we;
    assign dmem_addr  = dc_mem_req_addr;
    assign dmem_wdata = dc_mem_req_wdata;
    assign dmem_be    = dc_mem_req_be;
    assign dc_mem_resp_valid = dmem_valid & dmem_ready;
    assign dc_mem_resp_data  = dmem_rdata;

    // ------------------------------------------------------------------
    // I-cache instance.
    // ------------------------------------------------------------------
    icache u_icache (
        .clk            (clk),
        .rst_n          (rst_n),
        .cpu_req_valid  (fe_cpu_req_valid),
        .cpu_req_addr   (fe_cpu_req_addr),
        .cpu_req_ready  (ic_cpu_req_ready),
        .cpu_resp_valid (ic_cpu_resp_valid),
        .cpu_resp_data  (ic_cpu_resp_data),
        .flush          (redirect_valid),
        .flush_epoch    (flush_epoch_r),
        .mem_req_valid  (ic_mem_req_valid),
        .mem_req_addr   (ic_mem_req_addr),
        .mem_req_ready  (imem_ready),
        .mem_resp_valid (ic_mem_resp_valid),
        .mem_resp_data  (ic_mem_resp_data)
    );

    // ------------------------------------------------------------------
    // Branch predictor instance.
    // ------------------------------------------------------------------
    branch_predictor u_bp (
        .clk               (clk),
        .rst_n             (rst_n),
        .pred_req_valid    (fe_pred_req_valid),
        .pred_pc           (fe_pred_pc),
        .pred_hit          (bp_pred_hit),
        .pred_taken        (bp_pred_taken),
        .pred_target       (bp_pred_target),
        .resolve_valid     (rob_resolve_valid),
        .resolve_pc        (rob_resolve_pc),
        .resolve_next_pc   (rob_resolve_next_pc),
        .resolve_is_branch (rob_resolve_is_branch),
        .resolve_is_jump   (rob_resolve_is_jump),
        .resolve_taken     (rob_resolve_taken),
        .resolve_mispredict(rob_resolve_mispredict)
    );

    // ------------------------------------------------------------------
    // Fetch frontend instance.
    // ------------------------------------------------------------------
    fetch_frontend u_fetch (
        .clk              (clk),
        .rst_n            (rst_n),
        .redirect_valid   (redirect_valid),
        .redirect_pc      (redirect_pc),
        .stop_fetch       (stop_fetch),
        .halted           (halted_r),
        .cpu_req_valid    (fe_cpu_req_valid),
        .cpu_req_addr     (fe_cpu_req_addr),
        .cpu_req_ready    (ic_cpu_req_ready),
        .cpu_resp_valid   (ic_cpu_resp_valid),
        .cpu_resp_data    (ic_cpu_resp_data),
        .pred_req_valid   (fe_pred_req_valid),
        .pred_pc          (fe_pred_pc),
        .pred_hit         (bp_pred_hit),
        .pred_taken       (bp_pred_taken),
        .pred_target      (bp_pred_target),
        .dec_valid        (fe_dec_valid),
        .dec_pc           (fe_dec_pc),
        .dec_insn         (fe_dec_insn),
        .dec_pred_next_pc (fe_dec_pred_next_pc),
        .dec_pred_valid   (fe_dec_pred_valid),
        .dec_ready        (fe_dec_ready)
    );

    // ------------------------------------------------------------------
    // Decode unit instance.
    // ------------------------------------------------------------------
    decode_unit u_decode (
        .clk              (clk),
        .rst_n            (rst_n),
        .dec_valid        (fe_dec_valid),
        .dec_pc           (fe_dec_pc),
        .dec_insn         (fe_dec_insn),
        .dec_pred_next_pc (fe_dec_pred_next_pc),
        .dec_pred_valid   (fe_dec_pred_valid),
        .dec_rs1          (du_rs1),
        .dec_rs2          (du_rs2),
        .dec_rd           (du_rd),
        .dec_uses_rs1     (du_uses_rs1),
        .dec_uses_rs2     (du_uses_rs2),
        .dec_writes_rd    (du_writes_rd),
        .dec_is_branch    (du_is_branch),
        .dec_is_jump      (du_is_jump),
        .dec_is_ecall     (du_is_ecall),
        .dec_is_load      (du_is_load),
        .dec_is_store     (du_is_store),
        .dec_imm          (du_imm),
        .dec_funct3       (du_funct3),
        .dec_ready        (ru_dec_ready)
    );

    assign fe_dec_ready = ru_dec_ready;

    // ------------------------------------------------------------------
    // Rename unit instance.
    // ------------------------------------------------------------------
    rename_unit u_rename (
        .clk              (clk),
        .rst_n            (rst_n),
        .dec_valid        (fe_dec_valid),
        .dec_pc           (fe_dec_pc),
        .dec_insn         (fe_dec_insn),
        .dec_rs1          (du_rs1),
        .dec_rs2          (du_rs2),
        .dec_rd           (du_rd),
        .dec_uses_rs1     (du_uses_rs1),
        .dec_uses_rs2     (du_uses_rs2),
        .dec_writes_rd    (du_writes_rd),
        .dec_is_branch    (du_is_branch),
        .dec_is_jump      (du_is_jump),
        .dec_is_ecall     (du_is_ecall),
        .dec_is_load      (du_is_load),
        .dec_is_store     (du_is_store),
        .dec_imm          (du_imm),
        .dec_funct3       (du_funct3),
        .dec_pred_next_pc (fe_dec_pred_next_pc),
        .dec_pred_valid   (fe_dec_pred_valid),
        .rob_alloc_ready  (rob_alloc_ready),
        .rob_alloc_id     (rob_alloc_id),
        .iq_alloc_ready   (iq_alloc_ready),
        .lsq_alloc_ready  (lsu_alloc_ready),
        .dec_ready        (ru_dec_ready),
        .disp_rob_id      (ru_disp_rob_id),
        .disp_pc          (ru_disp_pc),
        .disp_insn        (ru_disp_insn),
        .disp_rs1         (ru_disp_rs1),
        .disp_rs2         (ru_disp_rs2),
        .disp_rd          (ru_disp_rd),
        .disp_ps1         (ru_disp_ps1),
        .disp_ps2         (ru_disp_ps2),
        .disp_pd          (ru_disp_pd),
        .disp_uses_rs1    (ru_disp_uses_rs1),
        .disp_uses_rs2    (ru_disp_uses_rs2),
        .disp_writes_rd   (ru_disp_writes_rd),
        .disp_is_branch   (ru_disp_is_branch),
        .disp_is_jump     (ru_disp_is_jump),
        .disp_is_ecall    (ru_disp_is_ecall),
        .disp_is_load     (ru_disp_is_load),
        .disp_is_store    (ru_disp_is_store),
        .disp_imm         (ru_disp_imm),
        .disp_funct3      (ru_disp_funct3),
        .disp_pred_next_pc(ru_disp_pred_next_pc),
        .disp_pred_valid  (ru_disp_pred_valid),
        .disp_old_pd      (ru_disp_old_pd),
        .disp_ckpt_id     (ru_disp_ckpt_id),
        .disp_alloc_ckpt  (ru_disp_alloc_ckpt),
        .ecall_barrier    (ru_ecall_barrier),
        .int_prf_raddr1   (ru_int_prf_raddr1),
        .int_prf_raddr2   (ru_int_prf_raddr2),
        .int_prf_rdata1   (ru_int_prf_rdata1),
        .int_prf_rdata2   (ru_int_prf_rdata2),
        .lsu_prf_raddr1   (ru_lsu_prf_raddr1),
        .lsu_prf_raddr2   (ru_lsu_prf_raddr2),
        .lsu_prf_rdata1   (ru_lsu_prf_rdata1),
        .lsu_prf_rdata2   (ru_lsu_prf_rdata2),
        .prf_ready_vec    (ru_prf_ready_vec),
        .int_wb_valid     (ie_wb_valid),
        .int_wb_pd        (ie_wb_pd),
        .int_wb_data      (ie_wb_data),
        .lsu_wb_valid     (lsu_wb_valid),
        .lsu_wb_pd        (lsu_wb_pd),
        .lsu_wb_data      (lsu_wb_data),
        .commit_valid     (rob_commit_valid),
        .commit_rd        (rob_commit_rd),
        .commit_pd        (rob_commit_pd),
        .commit_has_rd    (rob_commit_has_rd),
        .commit_old_pd    (rob_commit_old_pd),
        .squash_valid     (rob_squash_valid),
        .squash_ckpt_id   (rob_squash_ckpt_id),
        .dbg_reg_addr     (dbg_reg_addr),
        .dbg_reg_data     (ru_dbg_reg_data),
        .committed_x10    (ru_committed_x10)
    );

    // PRF read address mux: IQ drives int ports, LSU drives lsu ports.
    assign ru_int_prf_raddr1 = iq_prf_raddr1;
    assign ru_int_prf_raddr2 = iq_prf_raddr2;
    assign ru_lsu_prf_raddr1 = lsu_prf_raddr1;
    assign ru_lsu_prf_raddr2 = lsu_prf_raddr2;

    // ------------------------------------------------------------------
    // Dispatch routing: ALU/ECALL uops go to IQ, loads/stores go to LSU.
    // ------------------------------------------------------------------
    logic iq_alloc_valid;
    logic lsu_alloc_valid;
    assign iq_alloc_valid  = ru_dec_ready & ~(ru_disp_is_load | ru_disp_is_store);
    assign lsu_alloc_valid = ru_dec_ready &  (ru_disp_is_load | ru_disp_is_store);

    // ------------------------------------------------------------------
    // Issue queue instance.
    // ------------------------------------------------------------------
    issue_queue u_iq (
        .clk               (clk),
        .rst_n             (rst_n),
        .alloc_valid       (iq_alloc_valid),
        .alloc_rob_id      (ru_disp_rob_id),
        .alloc_pc          (ru_disp_pc),
        .alloc_insn        (ru_disp_insn),
        .alloc_ps1         (ru_disp_ps1),
        .alloc_ps2         (ru_disp_ps2),
        .alloc_pd          (ru_disp_pd),
        .alloc_uses_rs1    (ru_disp_uses_rs1),
        .alloc_uses_rs2    (ru_disp_uses_rs2),
        .alloc_writes_rd   (ru_disp_writes_rd),
        .alloc_is_branch   (ru_disp_is_branch),
        .alloc_is_jump     (ru_disp_is_jump),
        .alloc_pred_next_pc(ru_disp_pred_next_pc),
        .alloc_pred_valid  (ru_disp_pred_valid),
        .alloc_ready       (iq_alloc_ready),
        .prf_ready_vec     (ru_prf_ready_vec),
        .prf_raddr1        (iq_prf_raddr1),
        .prf_raddr2        (iq_prf_raddr2),
        .prf_rdata1        (ru_int_prf_rdata1),
        .prf_rdata2        (ru_int_prf_rdata2),
        .issue_valid       (iq_issue_valid),
        .issue_ready       (ie_issue_ready),
        .issue_rob_id      (iq_issue_rob_id),
        .issue_pc          (iq_issue_pc),
        .issue_insn        (iq_issue_insn),
        .issue_rs1_val     (iq_issue_rs1_val),
        .issue_rs2_val     (iq_issue_rs2_val),
        .issue_pd          (iq_issue_pd),
        .issue_writes_rd   (iq_issue_writes_rd),
        .issue_is_branch   (iq_issue_is_branch),
        .issue_is_jump     (iq_issue_is_jump),
        .issue_pred_next_pc(iq_issue_pred_next_pc),
        .issue_pred_valid  (iq_issue_pred_valid),
        .squash_valid      (rob_squash_valid),
        .squash_rob_head   (rob_squash_rob_head),
        .squash_rob_tail   (rob_squash_rob_tail)
    );

    // ------------------------------------------------------------------
    // Integer execute unit instance.
    // ------------------------------------------------------------------
    integer_execute_unit u_iex (
        .clk                        (clk),
        .rst_n                      (rst_n),
        .issue_valid                (iq_issue_valid),
        .issue_ready                (ie_issue_ready),
        .issue_rob_id               (iq_issue_rob_id),
        .issue_pc                   (iq_issue_pc),
        .issue_insn                 (iq_issue_insn),
        .issue_rs1_val              (iq_issue_rs1_val),
        .issue_rs2_val              (iq_issue_rs2_val),
        .issue_pd                   (iq_issue_pd),
        .issue_writes_rd            (iq_issue_writes_rd),
        .issue_is_branch            (iq_issue_is_branch),
        .issue_is_jump              (iq_issue_is_jump),
        .issue_pred_next_pc         (iq_issue_pred_next_pc),
        .issue_pred_valid           (iq_issue_pred_valid),
        .int_complete_valid         (ie_complete_valid),
        .int_complete_rob_id        (ie_complete_rob_id),
        .int_complete_pd            (ie_complete_pd),
        .int_complete_data          (ie_complete_data),
        .int_complete_actual_next_pc(ie_complete_actual_next_pc),
        .int_complete_taken         (ie_complete_taken),
        .int_wb_valid               (ie_wb_valid),
        .int_wb_pd                  (ie_wb_pd),
        .int_wb_data                (ie_wb_data)
    );

    // ------------------------------------------------------------------
    // Load/store unit instance.
    // ------------------------------------------------------------------
    load_store_unit u_lsu (
        .clk                     (clk),
        .rst_n                   (rst_n),
        .alloc_valid             (lsu_alloc_valid),
        .alloc_rob_id            (ru_disp_rob_id),
        .alloc_is_store          (ru_disp_is_store),
        .alloc_has_rd            (ru_disp_writes_rd),
        .alloc_ps1               (ru_disp_ps1),
        .alloc_ps2               (ru_disp_ps2),
        .alloc_pd                (ru_disp_pd),
        .alloc_uses_rs2          (ru_disp_uses_rs2),
        .alloc_imm               (ru_disp_imm),
        .alloc_funct3            (ru_disp_funct3),
        .alloc_ready             (lsu_alloc_ready),
        .lsu_prf_raddr1          (lsu_prf_raddr1),
        .lsu_prf_raddr2          (lsu_prf_raddr2),
        .lsu_prf_rdata1          (ru_lsu_prf_rdata1),
        .lsu_prf_rdata2          (ru_lsu_prf_rdata2),
        .prf_ready_vec           (ru_prf_ready_vec),
        .head_valid              (rob_head_valid),
        .head_rob_id             (rob_head_rob_id),
        .head_is_load            (rob_head_is_load),
        .head_is_store           (rob_head_is_store),
        .head_complete           (rob_head_complete),
        .cpu_req_valid           (lsu_cpu_req_valid),
        .cpu_req_we              (lsu_cpu_req_we),
        .cpu_req_addr            (lsu_cpu_req_addr),
        .cpu_req_wdata           (lsu_cpu_req_wdata),
        .cpu_req_be              (lsu_cpu_req_be),
        .cpu_req_ready           (dc_cpu_req_ready),
        .cpu_resp_valid          (dc_cpu_resp_valid),
        .cpu_resp_rdata          (dc_cpu_resp_rdata),
        .lsu_complete_valid      (lsu_complete_valid),
        .lsu_complete_rob_id     (lsu_complete_rob_id),
        .lsu_complete_pd         (lsu_complete_pd),
        .lsu_complete_data       (lsu_complete_data),
        .lsu_complete_writes_rd  (lsu_complete_writes_rd),
        .lsu_complete_is_store_done(lsu_complete_is_store_done),
        .store_accept_valid      (lsu_store_accept_valid),
        .store_accept_rob_id     (lsu_store_accept_rob_id),
        .lsu_wb_valid            (lsu_wb_valid),
        .lsu_wb_pd               (lsu_wb_pd),
        .lsu_wb_data             (lsu_wb_data),
        .load_waiting            (lsu_load_waiting),
        .load_waiting_rob_id     (lsu_load_waiting_rob_id),
        .squash_valid            (rob_squash_valid),
        .squash_rob_head         (rob_squash_rob_head),
        .squash_rob_tail         (rob_squash_rob_tail)
    );

    // ------------------------------------------------------------------
    // D-cache instance.
    // ------------------------------------------------------------------
    dcache u_dcache (
        .clk            (clk),
        .rst_n          (rst_n),
        .cpu_req_valid  (lsu_cpu_req_valid),
        .cpu_req_we     (lsu_cpu_req_we),
        .cpu_req_addr   (lsu_cpu_req_addr),
        .cpu_req_wdata  (lsu_cpu_req_wdata),
        .cpu_req_be     (lsu_cpu_req_be),
        .cpu_req_ready  (dc_cpu_req_ready),
        .cpu_resp_valid (dc_cpu_resp_valid),
        .cpu_resp_rdata (dc_cpu_resp_rdata),
        .flush          (redirect_valid),
        .flush_epoch    (flush_epoch_r),
        .mem_req_valid  (dc_mem_req_valid),
        .mem_req_we     (dc_mem_req_we),
        .mem_req_addr   (dc_mem_req_addr),
        .mem_req_wdata  (dc_mem_req_wdata),
        .mem_req_be     (dc_mem_req_be),
        .mem_req_ready  (dmem_ready),
        .mem_resp_valid (dc_mem_resp_valid),
        .mem_resp_data  (dc_mem_resp_data)
    );

    // ------------------------------------------------------------------
    // Reorder buffer instance.
    // ------------------------------------------------------------------
    reorder_buffer u_rob (
        .clk                     (clk),
        .rst_n                   (rst_n),
        .alloc_valid             (ru_dec_ready),
        .alloc_pc                (ru_disp_pc),
        .alloc_insn              (ru_disp_insn),
        .alloc_rd                (ru_disp_rd),
        .alloc_pd                (ru_disp_pd),
        .alloc_old_pd            (ru_disp_old_pd),
        .alloc_has_rd            (ru_disp_writes_rd),
        .alloc_is_branch         (ru_disp_is_branch),
        .alloc_is_jump           (ru_disp_is_jump),
        .alloc_is_ecall          (ru_disp_is_ecall),
        .alloc_is_load           (ru_disp_is_load),
        .alloc_is_store          (ru_disp_is_store),
        .alloc_pred_next_pc      (ru_disp_pred_next_pc),
        .alloc_pred_valid        (ru_disp_pred_valid),
        .alloc_ckpt_id           (ru_disp_ckpt_id),
        .alloc_ready             (rob_alloc_ready),
        .alloc_rob_id            (rob_alloc_id),
        .int_complete_valid      (ie_complete_valid),
        .int_complete_rob_id     (ie_complete_rob_id),
        .int_complete_pd         (ie_complete_pd),
        .int_complete_data       (ie_complete_data),
        .int_complete_actual_next_pc(ie_complete_actual_next_pc),
        .int_complete_taken      (ie_complete_taken),
        .lsu_complete_valid      (lsu_complete_valid),
        .lsu_complete_rob_id     (lsu_complete_rob_id),
        .lsu_complete_pd         (lsu_complete_pd),
        .lsu_complete_data       (lsu_complete_data),
        .lsu_complete_writes_rd  (lsu_complete_writes_rd),
        .lsu_complete_is_store_done(lsu_complete_is_store_done),
        .store_accept_valid      (lsu_store_accept_valid),
        .store_accept_rob_id     (lsu_store_accept_rob_id),
        .commit_x10              (ru_committed_x10),
        .commit_valid            (rob_commit_valid),
        .commit_rd               (rob_commit_rd),
        .commit_pd               (rob_commit_pd),
        .commit_has_rd           (rob_commit_has_rd),
        .commit_old_pd           (rob_commit_old_pd),
        .retire_valid            (rob_retire_valid),
        .retire_pc               (rob_retire_pc),
        .retire_insn             (rob_retire_insn),
        .head_valid              (rob_head_valid),
        .head_rob_id             (rob_head_rob_id),
        .head_is_load            (rob_head_is_load),
        .head_is_store           (rob_head_is_store),
        .head_complete           (rob_head_complete),
        .resolve_valid           (rob_resolve_valid),
        .resolve_mispredict      (rob_resolve_mispredict),
        .resolve_taken           (rob_resolve_taken),
        .resolve_pc              (rob_resolve_pc),
        .resolve_next_pc         (rob_resolve_next_pc),
        .resolve_is_branch       (rob_resolve_is_branch),
        .resolve_is_jump         (rob_resolve_is_jump),
        .squash_valid            (rob_squash_valid),
        .squash_rob_head         (rob_squash_rob_head),
        .squash_rob_tail         (rob_squash_rob_tail),
        .squash_ckpt_id          (rob_squash_ckpt_id),
        .squash_pc               (rob_squash_pc),
        .halt_req                (rob_halt_req),
        .halt_exit_code          (rob_halt_exit_code)
    );

    // ------------------------------------------------------------------
    // Debug / capability outputs.
    // ------------------------------------------------------------------
    assign dbg_reg_data = ru_dbg_reg_data;

    assign dbg_retire_valid = rst_n & ~halted_r & rob_retire_valid;
    assign dbg_retire_pc    = rob_retire_pc;
    assign dbg_retire_insn  = rob_retire_insn;

    assign dbg_bp_resolve    = rst_n & ~halted_r & rob_resolve_valid;
    assign dbg_bp_mispredict = rst_n & ~halted_r & rob_resolve_valid & rob_resolve_mispredict;

    // Real issue accept: integer IQ issue accepted, or LSU dispatch accept.
    logic iq_issue_accept;
    logic lsu_disp_accept;
    assign iq_issue_accept  = iq_issue_valid & ie_issue_ready;
    assign lsu_disp_accept  = ru_dec_ready & (ru_disp_is_load | ru_disp_is_store);

    assign dbg_ooo_issue = rst_n & ~halted_r & (iq_issue_accept | lsu_disp_accept);

    // Bypass: younger independent ALU issues while older load waits on the
    // external D bus (dmem_valid && !dmem_ready).
    logic younger_than_load;
    assign younger_than_load = ((iq_issue_rob_id - lsu_load_waiting_rob_id) & 3'b111) != 3'b000;
    assign dbg_ooo_bypass = rst_n & ~halted_r & iq_issue_accept &
                            lsu_load_waiting & dmem_valid & ~dmem_ready &
                            younger_than_load;

    // ------------------------------------------------------------------
    // Halt / exit-code / flush-epoch sequential control.
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (!rst_n) begin
            halted_r      <= 1'b0;
            exit_code_r   <= 32'd0;
            flush_epoch_r <= 2'd0;
        end else begin
            if (rob_halt_req) begin
                halted_r    <= 1'b1;
                exit_code_r <= rob_halt_exit_code;
            end
            if (redirect_valid | rob_halt_req) begin
                flush_epoch_r <= flush_epoch_r + 2'd1;
            end
        end
    end

    assign halted    = halted_r;
    assign exit_code = exit_code_r;

endmodule
