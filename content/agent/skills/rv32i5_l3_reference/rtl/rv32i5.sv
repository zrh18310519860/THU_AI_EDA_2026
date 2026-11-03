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

    logic        icache_cpu_req_ready;
    logic        icache_cpu_resp_valid;
    logic [31:0] icache_cpu_resp_data;
    logic        icache_mem_req_valid;
    logic [31:0] icache_mem_req_addr;
    logic        icache_flush;
    logic [1:0]  icache_flush_epoch;
    logic        fe_icache_cpu_req_valid;
    logic [31:0] fe_icache_cpu_req_addr;

    logic        dcache_cpu_req_valid;
    logic        dcache_cpu_req_we;
    logic [31:0] dcache_cpu_req_addr;
    logic [31:0] dcache_cpu_req_wdata;
    logic [3:0]  dcache_cpu_req_be;
    logic        dcache_cpu_req_ready;
    logic        dcache_cpu_resp_valid;
    logic [31:0] dcache_cpu_resp_rdata;
    logic        dcache_mem_req_valid;
    logic [31:0] dcache_mem_req_addr;
    logic        dcache_mem_req_we;
    logic [31:0] dcache_mem_req_wdata;
    logic [3:0]  dcache_mem_req_be;
    logic        dcache_mem_waiting;
    logic        dcache_flush;
    logic [1:0]  dcache_flush_epoch;

    logic        bp_lookup_valid;
    logic [31:0] bp_lookup_pc;
    logic        bp_pred_valid;
    logic        bp_pred_taken;
    logic [31:0] bp_pred_target;
    logic [1:0]  bp_pred_type;
    logic        bp_update_valid;
    logic [31:0] bp_update_pc;
    logic        bp_update_actual_taken;
    logic [31:0] bp_update_actual_target;
    logic        bp_update_is_branch;
    logic        bp_update_is_jal;
    logic        bp_update_is_jalr;
    logic [31:0] bp_update_pred_next_pc;
    logic        bp_update_pred_valid;
    logic        bp_resolve_pulse;
    logic        bp_mispredict_pulse;
    logic [31:0] bp_mispredict_target;

    logic        fetch_valid;
    logic [31:0] fetch_pc;
    logic [31:0] fetch_insn;
    logic [31:0] fetch_pred_next_pc;
    logic        fetch_pred_valid;
    logic [1:0]  fetch_pred_type;
    logic        fetch_ready;
    logic        redirect_valid;
    logic [31:0] redirect_pc;
    logic [1:0]  redirect_epoch;
    logic        stop_fetch;

    logic        uop_valid;
    logic        uop_ready;
    logic [31:0] uop_pc;
    logic [31:0] uop_insn;
    logic [4:0]  uop_rs1;
    logic [4:0]  uop_rs2;
    logic [4:0]  uop_rd;
    logic [31:0] uop_imm;
    logic [2:0]  uop_funct3;
    logic [4:0]  uop_alu_op;
    logic [15:0] uop_ctrl;
    logic [31:0] uop_pred_next_pc;
    logic        uop_pred_valid;
    logic [1:0]  uop_pred_type;

    logic        disp_valid;
    logic        disp_iq_valid;
    logic [31:0] disp_pc;
    logic [31:0] disp_insn;
    logic [15:0] disp_ctrl;
    logic [2:0]  disp_funct3;
    logic [4:0]  disp_alu_op;
    logic [31:0] disp_imm;
    logic [4:0]  disp_rs1;
    logic [4:0]  disp_rs2;
    logic [4:0]  disp_rd;
    logic [5:0]  disp_prs1;
    logic [5:0]  disp_prs2;
    logic [5:0]  disp_prd;
    logic [5:0]  disp_old_prd;
    logic [3:0]  disp_rob_id;
    logic [31:0] disp_pred_next_pc;
    logic        disp_pred_valid;
    logic [1:0]  disp_pred_type;
    logic        rob_disp_ready;
    logic [3:0]  rob_alloc_id;
    logic        disp_iq_ready;
    logic        rename_ecall_pending;
    logic [39:0] prf_ready;
    logic [31:0] prf_rdata0;
    logic [31:0] prf_rdata1;
    logic [31:0] rename_dbg_reg_data;
    logic [31:0] arch_x10;

    logic        iq_prf_rd_en0;
    logic [5:0]  iq_prf_rd_addr0;
    logic        iq_prf_rd_en1;
    logic [5:0]  iq_prf_rd_addr1;

    logic        issue_valid;
    logic        issue_ready;
    logic [31:0] issue_pc;
    logic [31:0] issue_insn;
    logic [15:0] issue_ctrl;
    logic [2:0]  issue_funct3;
    logic [4:0]  issue_alu_op;
    logic [31:0] issue_imm;
    logic [31:0] issue_src1;
    logic [31:0] issue_src2;
    logic [5:0]  issue_prd;
    logic [3:0]  issue_rob_id;
    logic [31:0] issue_pred_next_pc;
    logic        issue_pred_valid;
    logic [1:0]  issue_pred_type;
    logic        issue_is_mem;

    logic        rob_commit_valid;
    logic [3:0]  rob_commit_rob_id;
    logic [31:0] rob_commit_pc;
    logic [31:0] rob_commit_insn;
    logic [4:0]  rob_commit_rd;
    logic [5:0]  rob_commit_prd;
    logic [5:0]  rob_commit_old_prd;
    logic        rob_commit_rd_we;
    logic        rob_commit_is_store;
    logic        rob_commit_is_ecall;
    logic [3:0]  rob_head_id;
    logic        rob_full;
    logic        rob_empty;
    logic        rob_head_complete;
    logic        rob_head_is_store;
    logic        rob_head_is_ecall;
    logic        rob_ecall_pending;
    logic        rob_retire_valid;
    logic [31:0] rob_retire_pc;
    logic [31:0] rob_retire_insn;

    logic        lsu_issue_ready;
    logic        lsu_complete_valid;
    logic [3:0]  lsu_complete_rob_id;
    logic [5:0]  lsu_complete_prd;
    logic [31:0] lsu_complete_data;
    logic        lsu_complete_we;
    logic        lsu_load_waiting;

    logic        complete_valid;
    logic [3:0]  complete_rob_id;
    logic [5:0]  complete_prd;
    logic [31:0] complete_data;
    logic        complete_we;

    logic        ex_valid;
    logic [31:0] ex_result;
    logic [31:0] ex_actual_next_pc;
    logic        ex_branch_taken;
    logic        ex_is_control;
    logic        ex_mispredict;
    logic        ex_redirect_valid;
    logic [31:0] ex_redirect_pc;
    logic [31:0] ex_complete_data;

    logic [1:0]  flush_epoch;
    logic        halted_reg;
    logic [31:0] exit_code_reg;
    logic        halt_pending;
    logic [31:0] imem_addr_hold;

    logic        ecall_dispatch;
    logic        ecall_commit;
    logic        branch_redirect;
    logic        branch_squash;
    logic        epoch_event;
    logic        squash_valid;
    logic [3:0]  squash_rob_id;
    logic        squash_restore;
    logic        bus_idle_accept;
    logic        alu_issue_accept;

    assign ecall_dispatch = disp_valid && disp_ctrl[8] && !halted_reg && !halt_pending;
    assign ecall_commit   = rob_commit_valid && rob_commit_is_ecall && !halted_reg;
    assign branch_squash  = ex_valid && ex_is_control && ex_mispredict;
    assign branch_redirect = ex_redirect_valid && ex_valid && ex_is_control &&
                             !ecall_commit && !halt_pending && !halted_reg;

    assign epoch_event = branch_redirect || ecall_dispatch;

    always @(posedge clk) begin
        if (!rst_n) begin
            flush_epoch <= 2'd0;
        end else if (epoch_event) begin
            flush_epoch <= flush_epoch + 2'd1;
        end
    end

    assign icache_flush       = branch_redirect || ecall_dispatch || ecall_commit;
    assign icache_flush_epoch = flush_epoch;
    assign dcache_flush       = 1'b0;
    assign dcache_flush_epoch = 2'b0;

    assign redirect_valid = branch_redirect;
    assign redirect_pc    = ex_redirect_pc;
    assign redirect_epoch = flush_epoch + (epoch_event ? 2'd1 : 2'd0);

    assign stop_fetch = rename_ecall_pending || rob_ecall_pending ||
                        halted_reg || halt_pending || ecall_commit;

    assign squash_valid = ecall_commit || branch_squash;
    assign squash_rob_id = ecall_commit ? rob_commit_rob_id : issue_rob_id;
    assign squash_restore = ecall_commit ? 1'b0 : branch_squash;

    assign bus_idle_accept = (!icache_mem_req_valid || imem_ready) &&
                             (!dcache_mem_req_valid || dmem_ready);

    always @(posedge clk) begin
        if (!rst_n) begin
            halted_reg    <= 1'b0;
            exit_code_reg <= 32'd0;
            halt_pending  <= 1'b0;
        end else if (!halted_reg) begin
            if (ecall_commit) begin
                halt_pending <= 1'b1;
            end
            if (halt_pending && bus_idle_accept) begin
                halted_reg    <= 1'b1;
                exit_code_reg <= arch_x10;
                halt_pending  <= 1'b0;
            end
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            imem_addr_hold <= 32'd0;
        end else if (!halted_reg && icache_mem_req_valid) begin
            imem_addr_hold <= icache_mem_req_addr;
        end
    end

    assign issue_ready = issue_is_mem ? lsu_issue_ready : !lsu_complete_valid;
    assign alu_issue_accept = issue_valid && issue_ready && !issue_is_mem;
    assign ex_valid = alu_issue_accept;

    assign complete_valid  = lsu_complete_valid || alu_issue_accept;
    assign complete_rob_id = lsu_complete_valid ? lsu_complete_rob_id : issue_rob_id;
    assign complete_prd    = lsu_complete_valid ? lsu_complete_prd    : issue_prd;
    assign complete_data   = lsu_complete_valid ? lsu_complete_data   : ex_complete_data;
    assign complete_we     = lsu_complete_valid ? lsu_complete_we :
                             (alu_issue_accept && issue_ctrl[2] && (issue_prd != 6'd0));

    assign bp_update_valid         = ex_valid && ex_is_control;
    assign bp_update_pc           = issue_pc;
    assign bp_update_actual_taken = ex_branch_taken;
    assign bp_update_actual_target = ex_actual_next_pc;
    assign bp_update_is_branch    = issue_ctrl[5];
    assign bp_update_is_jal       = issue_ctrl[6];
    assign bp_update_is_jalr      = issue_ctrl[7];
    assign bp_update_pred_next_pc = issue_pred_next_pc;
    assign bp_update_pred_valid   = issue_pred_valid;

    assign imem_valid = icache_mem_req_valid && rst_n && !halted_reg;
    assign dmem_valid = dcache_mem_req_valid && rst_n && !halted_reg;
    assign imem_addr  = (!rst_n) ? 32'h0 : (halted_reg ? imem_addr_hold : icache_mem_req_addr);
    assign dmem_we    = dcache_mem_req_we;
    assign dmem_addr  = dcache_mem_req_addr;
    assign dmem_wdata = dcache_mem_req_wdata;
    assign dmem_be    = dcache_mem_req_be;

    assign dbg_reg_data      = rename_dbg_reg_data;
    assign halted            = halted_reg;
    assign exit_code         = exit_code_reg;
    assign dbg_features      = 3'b111;
    assign dbg_retire_valid  = rob_retire_valid && rst_n && !halted_reg;
    assign dbg_retire_pc     = rob_retire_pc;
    assign dbg_retire_insn   = rob_retire_insn;
    assign dbg_bp_resolve    = bp_resolve_pulse && rst_n && !halted_reg;
    assign dbg_bp_mispredict = bp_mispredict_pulse && rst_n && !halted_reg;
    assign dbg_ooo_issue     = (issue_valid && issue_ready) && rst_n && !halted_reg;
    assign dbg_ooo_bypass    = (issue_valid && issue_ready && !issue_is_mem && lsu_load_waiting) &&
                               rst_n && !halted_reg;

    icache u_icache (
        .clk(clk),
        .rst_n(rst_n),
        .cpu_req_valid(fe_icache_cpu_req_valid),
        .cpu_req_addr(fe_icache_cpu_req_addr),
        .cpu_req_ready(icache_cpu_req_ready),
        .cpu_resp_valid(icache_cpu_resp_valid),
        .cpu_resp_data(icache_cpu_resp_data),
        .flush(icache_flush),
        .flush_epoch(icache_flush_epoch),
        .mem_req_valid(icache_mem_req_valid),
        .mem_req_ready(imem_ready),
        .mem_req_addr(icache_mem_req_addr),
        .mem_resp_valid(icache_mem_req_valid && imem_ready && rst_n && !halted_reg),
        .mem_resp_data(imem_rdata)
    );

    dcache u_dcache (
        .clk(clk),
        .rst_n(rst_n),
        .cpu_req_valid(dcache_cpu_req_valid),
        .cpu_req_we(dcache_cpu_req_we),
        .cpu_req_addr(dcache_cpu_req_addr),
        .cpu_req_wdata(dcache_cpu_req_wdata),
        .cpu_req_be(dcache_cpu_req_be),
        .cpu_req_ready(dcache_cpu_req_ready),
        .cpu_resp_valid(dcache_cpu_resp_valid),
        .cpu_resp_rdata(dcache_cpu_resp_rdata),
        .flush(dcache_flush),
        .flush_epoch(dcache_flush_epoch),
        .mem_req_valid(dcache_mem_req_valid),
        .mem_req_ready(dmem_ready),
        .mem_req_addr(dcache_mem_req_addr),
        .mem_req_we(dcache_mem_req_we),
        .mem_req_wdata(dcache_mem_req_wdata),
        .mem_req_be(dcache_mem_req_be),
        .mem_resp_valid(dcache_mem_req_valid && dmem_ready && rst_n && !halted_reg),
        .mem_resp_data(dmem_rdata),
        .mem_waiting(dcache_mem_waiting)
    );

    branch_predictor u_branch_predictor (
        .clk(clk),
        .rst_n(rst_n),
        .bp_lookup_valid(bp_lookup_valid),
        .bp_lookup_pc(bp_lookup_pc),
        .bp_pred_valid(bp_pred_valid),
        .bp_pred_taken(bp_pred_taken),
        .bp_pred_target(bp_pred_target),
        .bp_pred_type(bp_pred_type),
        .bp_update_valid(bp_update_valid),
        .bp_update_pc(bp_update_pc),
        .bp_update_actual_taken(bp_update_actual_taken),
        .bp_update_actual_target(bp_update_actual_target),
        .bp_update_is_branch(bp_update_is_branch),
        .bp_update_is_jal(bp_update_is_jal),
        .bp_update_is_jalr(bp_update_is_jalr),
        .bp_update_pred_next_pc(bp_update_pred_next_pc),
        .bp_update_pred_valid(bp_update_pred_valid),
        .bp_resolve_pulse(bp_resolve_pulse),
        .bp_mispredict_pulse(bp_mispredict_pulse),
        .bp_mispredict_target(bp_mispredict_target)
    );

    frontend_unit u_frontend_unit (
        .clk(clk),
        .rst_n(rst_n),
        .icache_cpu_req_valid(fe_icache_cpu_req_valid),
        .icache_cpu_req_addr(fe_icache_cpu_req_addr),
        .icache_cpu_req_ready(icache_cpu_req_ready),
        .icache_cpu_resp_valid(icache_cpu_resp_valid),
        .icache_cpu_resp_data(icache_cpu_resp_data),
        .bp_lookup_valid(bp_lookup_valid),
        .bp_lookup_pc(bp_lookup_pc),
        .bp_pred_valid(bp_pred_valid),
        .bp_pred_taken(bp_pred_taken),
        .bp_pred_target(bp_pred_target),
        .bp_pred_type(bp_pred_type),
        .redirect_valid(redirect_valid),
        .redirect_pc(redirect_pc),
        .redirect_epoch(redirect_epoch),
        .stop_fetch(stop_fetch),
        .fetch_valid(fetch_valid),
        .fetch_ready(fetch_ready),
        .fetch_pc(fetch_pc),
        .fetch_insn(fetch_insn),
        .fetch_pred_next_pc(fetch_pred_next_pc),
        .fetch_pred_valid(fetch_pred_valid),
        .fetch_pred_type(fetch_pred_type)
    );

    decode_unit u_decode_unit (
        .fetch_valid(fetch_valid),
        .fetch_pc(fetch_pc),
        .fetch_insn(fetch_insn),
        .fetch_pred_next_pc(fetch_pred_next_pc),
        .fetch_pred_valid(fetch_pred_valid),
        .fetch_pred_type(fetch_pred_type),
        .uop_ready(uop_ready),
        .fetch_ready(fetch_ready),
        .uop_valid(uop_valid),
        .uop_pc(uop_pc),
        .uop_insn(uop_insn),
        .uop_rs1(uop_rs1),
        .uop_rs2(uop_rs2),
        .uop_rd(uop_rd),
        .uop_imm(uop_imm),
        .uop_funct3(uop_funct3),
        .uop_alu_op(uop_alu_op),
        .uop_ctrl(uop_ctrl),
        .uop_pred_next_pc(uop_pred_next_pc),
        .uop_pred_valid(uop_pred_valid),
        .uop_pred_type(uop_pred_type)
    );

    rename_unit u_rename_unit (
        .clk(clk),
        .rst_n(rst_n),
        .uop_valid(uop_valid),
        .uop_ready(uop_ready),
        .uop_pc(uop_pc),
        .uop_insn(uop_insn),
        .uop_rs1(uop_rs1),
        .uop_rs2(uop_rs2),
        .uop_rd(uop_rd),
        .uop_imm(uop_imm),
        .uop_funct3(uop_funct3),
        .uop_alu_op(uop_alu_op),
        .uop_ctrl(uop_ctrl),
        .uop_pred_next_pc(uop_pred_next_pc),
        .uop_pred_valid(uop_pred_valid),
        .uop_pred_type(uop_pred_type),
        .disp_rob_ready(rob_disp_ready),
        .rob_alloc_id(rob_alloc_id),
        .disp_iq_ready(disp_iq_ready),
        .disp_valid(disp_valid),
        .disp_iq_valid(disp_iq_valid),
        .disp_pc(disp_pc),
        .disp_insn(disp_insn),
        .disp_ctrl(disp_ctrl),
        .disp_funct3(disp_funct3),
        .disp_alu_op(disp_alu_op),
        .disp_imm(disp_imm),
        .disp_rs1(disp_rs1),
        .disp_rs2(disp_rs2),
        .disp_rd(disp_rd),
        .disp_prs1(disp_prs1),
        .disp_prs2(disp_prs2),
        .disp_prd(disp_prd),
        .disp_old_prd(disp_old_prd),
        .disp_rob_id(disp_rob_id),
        .disp_pred_next_pc(disp_pred_next_pc),
        .disp_pred_valid(disp_pred_valid),
        .disp_pred_type(disp_pred_type),
        .complete_valid(complete_valid),
        .complete_prd(complete_prd),
        .complete_data(complete_data),
        .complete_we(complete_we),
        .commit_valid(rob_commit_valid),
        .commit_prd(rob_commit_prd),
        .commit_old_prd(rob_commit_old_prd),
        .commit_rd(rob_commit_rd),
        .commit_rd_we(rob_commit_rd_we),
        .squash_valid(squash_valid),
        .squash_rob_id(squash_rob_id),
        .squash_restore(squash_restore),
        .ecall_pending(rename_ecall_pending),
        .prf_ready(prf_ready),
        .prf_rd_en0(iq_prf_rd_en0),
        .prf_rd_addr0(iq_prf_rd_addr0),
        .prf_rdata0(prf_rdata0),
        .prf_rd_en1(iq_prf_rd_en1),
        .prf_rd_addr1(iq_prf_rd_addr1),
        .prf_rdata1(prf_rdata1),
        .dbg_reg_addr(dbg_reg_addr),
        .dbg_reg_data(rename_dbg_reg_data),
        .arch_x10(arch_x10),
        .rob_head_id(rob_head_id),
        .commit_rob_id(rob_commit_rob_id)
    );

    issue_queue u_issue_queue (
        .clk(clk),
        .rst_n(rst_n),
        .halted(halted_reg),
        .disp_iq_valid(disp_iq_valid),
        .disp_iq_ready(disp_iq_ready),
        .disp_pc(disp_pc),
        .disp_insn(disp_insn),
        .disp_ctrl(disp_ctrl),
        .disp_funct3(disp_funct3),
        .disp_alu_op(disp_alu_op),
        .disp_imm(disp_imm),
        .disp_rs1(disp_rs1),
        .disp_rs2(disp_rs2),
        .disp_prs1(disp_prs1),
        .disp_prs2(disp_prs2),
        .disp_prd(disp_prd),
        .disp_rob_id(disp_rob_id),
        .disp_pred_next_pc(disp_pred_next_pc),
        .disp_pred_valid(disp_pred_valid),
        .disp_pred_type(disp_pred_type),
        .prf_ready(prf_ready),
        .prf_rd_en0(iq_prf_rd_en0),
        .prf_rd_addr0(iq_prf_rd_addr0),
        .prf_rdata0(prf_rdata0),
        .prf_rd_en1(iq_prf_rd_en1),
        .prf_rd_addr1(iq_prf_rd_addr1),
        .prf_rdata1(prf_rdata1),
        .rob_head_id(rob_head_id),
        .squash_valid(squash_valid),
        .squash_rob_id(squash_rob_id),
        .issue_valid(issue_valid),
        .issue_ready(issue_ready),
        .issue_pc(issue_pc),
        .issue_insn(issue_insn),
        .issue_ctrl(issue_ctrl),
        .issue_funct3(issue_funct3),
        .issue_alu_op(issue_alu_op),
        .issue_imm(issue_imm),
        .issue_src1(issue_src1),
        .issue_src2(issue_src2),
        .issue_prd(issue_prd),
        .issue_rob_id(issue_rob_id),
        .issue_pred_next_pc(issue_pred_next_pc),
        .issue_pred_valid(issue_pred_valid),
        .issue_pred_type(issue_pred_type),
        .issue_is_mem(issue_is_mem)
    );

    reorder_buffer u_reorder_buffer (
        .clk(clk),
        .rst_n(rst_n),
        .halted(halted_reg),
        .disp_valid(disp_valid),
        .disp_rob_ready(rob_disp_ready),
        .rob_alloc_id(rob_alloc_id),
        .disp_pc(disp_pc),
        .disp_insn(disp_insn),
        .disp_ctrl(disp_ctrl),
        .disp_rd(disp_rd),
        .disp_prd(disp_prd),
        .disp_old_prd(disp_old_prd),
        .disp_pred_next_pc(disp_pred_next_pc),
        .disp_pred_valid(disp_pred_valid),
        .disp_pred_type(disp_pred_type),
        .complete_valid(complete_valid),
        .complete_rob_id(complete_rob_id),
        .squash_valid(squash_valid),
        .squash_rob_id(squash_rob_id),
        .commit_valid(rob_commit_valid),
        .commit_rob_id(rob_commit_rob_id),
        .commit_pc(rob_commit_pc),
        .commit_insn(rob_commit_insn),
        .commit_rd(rob_commit_rd),
        .commit_prd(rob_commit_prd),
        .commit_old_prd(rob_commit_old_prd),
        .commit_rd_we(rob_commit_rd_we),
        .commit_is_store(rob_commit_is_store),
        .commit_is_ecall(rob_commit_is_ecall),
        .rob_head_id(rob_head_id),
        .rob_full(rob_full),
        .rob_empty(rob_empty),
        .head_complete(rob_head_complete),
        .head_is_store(rob_head_is_store),
        .head_is_ecall(rob_head_is_ecall),
        .ecall_pending(rob_ecall_pending),
        .retire_valid(rob_retire_valid),
        .retire_pc(rob_retire_pc),
        .retire_insn(rob_retire_insn)
    );

    load_store_unit u_load_store_unit (
        .clk(clk),
        .rst_n(rst_n),
        .halted(halted_reg),
        .issue_valid(issue_valid && issue_is_mem),
        .issue_ready(lsu_issue_ready),
        .issue_pc(issue_pc),
        .issue_insn(issue_insn),
        .issue_ctrl(issue_ctrl),
        .issue_funct3(issue_funct3),
        .issue_imm(issue_imm),
        .issue_src1(issue_src1),
        .issue_src2(issue_src2),
        .issue_prd(issue_prd),
        .issue_rob_id(issue_rob_id),
        .rob_head_id(rob_head_id),
        .squash_valid(squash_valid),
        .squash_rob_id(squash_rob_id),
        .dcache_cpu_req_valid(dcache_cpu_req_valid),
        .dcache_cpu_req_we(dcache_cpu_req_we),
        .dcache_cpu_req_addr(dcache_cpu_req_addr),
        .dcache_cpu_req_wdata(dcache_cpu_req_wdata),
        .dcache_cpu_req_be(dcache_cpu_req_be),
        .dcache_cpu_req_ready(dcache_cpu_req_ready),
        .dcache_cpu_resp_valid(dcache_cpu_resp_valid),
        .dcache_cpu_resp_rdata(dcache_cpu_resp_rdata),
        .dcache_mem_waiting(dcache_mem_waiting),
        .complete_valid(lsu_complete_valid),
        .complete_rob_id(lsu_complete_rob_id),
        .complete_prd(lsu_complete_prd),
        .complete_data(lsu_complete_data),
        .complete_we(lsu_complete_we),
        .lsu_load_waiting(lsu_load_waiting)
    );

    execute_unit u_execute_unit (
        .ex_valid(ex_valid),
        .ex_pc(issue_pc),
        .ex_insn(issue_insn),
        .ex_ctrl(issue_ctrl),
        .ex_funct3(issue_funct3),
        .ex_alu_op(issue_alu_op),
        .ex_imm(issue_imm),
        .ex_src1(issue_src1),
        .ex_src2(issue_src2),
        .ex_pred_next_pc(issue_pred_next_pc),
        .ex_pred_valid(issue_pred_valid),
        .ex_result(ex_result),
        .ex_actual_next_pc(ex_actual_next_pc),
        .ex_branch_taken(ex_branch_taken),
        .ex_is_control(ex_is_control),
        .ex_mispredict(ex_mispredict),
        .ex_redirect_valid(ex_redirect_valid),
        .ex_redirect_pc(ex_redirect_pc),
        .ex_complete_data(ex_complete_data)
    );

endmodule
