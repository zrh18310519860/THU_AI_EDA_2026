module load_store_unit (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        alloc_valid,
    input  logic [2:0]  alloc_rob_id,
    input  logic        alloc_is_store,
    input  logic        alloc_has_rd,
    input  logic [5:0]  alloc_ps1,
    input  logic [5:0]  alloc_ps2,
    input  logic [5:0]  alloc_pd,
    input  logic        alloc_uses_rs2,
    input  logic [31:0] alloc_imm,
    input  logic [2:0]  alloc_funct3,
    output logic        alloc_ready,
    output logic [5:0]  lsu_prf_raddr1,
    output logic [5:0]  lsu_prf_raddr2,
    input  logic [31:0] lsu_prf_rdata1,
    input  logic [31:0] lsu_prf_rdata2,
    input  logic [39:0] prf_ready_vec,
    input  logic        head_valid,
    input  logic [2:0]  head_rob_id,
    input  logic        head_is_load,
    input  logic        head_is_store,
    input  logic        head_complete,
    output logic        cpu_req_valid,
    output logic        cpu_req_we,
    output logic [31:0] cpu_req_addr,
    output logic [31:0] cpu_req_wdata,
    output logic [3:0]  cpu_req_be,
    input  logic        cpu_req_ready,
    input  logic        cpu_resp_valid,
    input  logic [31:0] cpu_resp_rdata,
    output logic        lsu_complete_valid,
    output logic [2:0]  lsu_complete_rob_id,
    output logic [5:0]  lsu_complete_pd,
    output logic [31:0] lsu_complete_data,
    output logic        lsu_complete_writes_rd,
    output logic        lsu_complete_is_store_done,
    output logic        store_accept_valid,
    output logic [2:0]  store_accept_rob_id,
    output logic        lsu_wb_valid,
    output logic [5:0]  lsu_wb_pd,
    output logic [31:0] lsu_wb_data,
    output logic        load_waiting,
    output logic [2:0]  load_waiting_rob_id,
    input  logic        squash_valid,
    input  logic [2:0]  squash_rob_head,
    input  logic [2:0]  squash_rob_tail
);

    // ------------------------------------------------------------------
    // 2-entry ordered LSQ. Memory ops launch only when the entry matches
    // the ROB head. Drives the dcache CPU port. Loads extend per funct3;
    // stores build byte lanes from the original address offset.
    // ------------------------------------------------------------------

    localparam integer NENT = 2;

    // Entry storage
    logic        e_valid   [0:NENT-1];
    logic [2:0]  e_rob_id  [0:NENT-1];
    logic        e_is_store[0:NENT-1];
    logic        e_has_rd  [0:NENT-1];
    logic [5:0]  e_ps1     [0:NENT-1];
    logic [5:0]  e_ps2     [0:NENT-1];
    logic [5:0]  e_pd      [0:NENT-1];
    logic        e_uses_rs2[0:NENT-1];
    logic [31:0] e_imm     [0:NENT-1];
    logic [2:0]  e_funct3  [0:NENT-1];
    logic        e_launched[0:NENT-1];

    // Registered launch context: identifies the entry whose request is in flight.
    logic        launched_valid;
    logic [2:0]  launched_rob_id;
    logic [5:0]  launched_pd;
    logic        launched_is_store;
    logic        launched_has_rd;
    logic [2:0]  launched_funct3;
    logic [1:0]  launched_byte_off;

    // ------------------------------------------------------------------
    // Find the entry matching the ROB head (only one can match).
    // ------------------------------------------------------------------
    logic        match_found;
    logic        match_idx;
    logic        match_is_store;
    logic        match_has_rd;
    logic [5:0]  match_ps1;
    logic [5:0]  match_ps2;
    logic [5:0]  match_pd;
    logic        match_uses_rs2;
    logic [31:0] match_imm;
    logic [2:0]  match_funct3;
    logic        match_launched;
    logic [2:0]  match_rob_id;

    always @* begin
        match_found    = 1'b0;
        match_idx      = 1'b0;
        match_is_store = 1'b0;
        match_has_rd   = 1'b0;
        match_ps1      = 6'b0;
        match_ps2      = 6'b0;
        match_pd       = 6'b0;
        match_uses_rs2 = 1'b0;
        match_imm      = 32'b0;
        match_funct3   = 3'b0;
        match_launched = 1'b0;
        match_rob_id   = 3'b0;
        for (int i = 0; i < NENT; i = i + 1) begin
            if (e_valid[i] && (e_rob_id[i] == head_rob_id)) begin
                match_found    = 1'b1;
                match_idx      = i[0];
                match_is_store = e_is_store[i];
                match_has_rd   = e_has_rd[i];
                match_ps1      = e_ps1[i];
                match_ps2      = e_ps2[i];
                match_pd       = e_pd[i];
                match_uses_rs2 = e_uses_rs2[i];
                match_imm      = e_imm[i];
                match_funct3   = e_funct3[i];
                match_launched = e_launched[i];
                match_rob_id   = e_rob_id[i];
            end
        end
    end

    // ------------------------------------------------------------------
    // PRF read addresses: driven from the matching entry's source PRs.
    // ------------------------------------------------------------------
    assign lsu_prf_raddr1 = match_ps1;
    assign lsu_prf_raddr2 = match_ps2;

    // ------------------------------------------------------------------
    // Operand readiness
    // ------------------------------------------------------------------
    logic src1_ready;
    logic src2_ready;
    assign src1_ready = prf_ready_vec[match_ps1];
    assign src2_ready = (!match_uses_rs2) || prf_ready_vec[match_ps2];

    // ------------------------------------------------------------------
    // Effective address and store data / byte lanes
    // ------------------------------------------------------------------
    logic [31:0] eff_addr;
    assign eff_addr = lsu_prf_rdata1 + match_imm;

    logic [31:0] store_wdata;
    logic [3:0]  store_be;
    logic [1:0]  byte_off;
    assign byte_off = eff_addr[1:0];

    always @* begin
        store_wdata = 32'b0;
        store_be    = 4'b0;
        case (match_funct3)
            3'b000: begin // SB
                store_wdata = {4{lsu_prf_rdata2[7:0]}};
                store_be    = 4'b0001 << byte_off;
            end
            3'b001: begin // SH
                store_wdata = {2{lsu_prf_rdata2[15:0]}};
                store_be    = byte_off[1] ? 4'b1100 : 4'b0011;
            end
            3'b010: begin // SW
                store_wdata = lsu_prf_rdata2;
                store_be    = 4'b1111;
            end
            default: begin
                store_wdata = lsu_prf_rdata2;
                store_be    = 4'b1111;
            end
        endcase
    end

    // ------------------------------------------------------------------
    // Launch condition: matching head entry, not yet launched, sources ready.
    // ------------------------------------------------------------------
    logic launch;
    assign launch = match_found && !match_launched && src1_ready && src2_ready
                    && head_valid && (head_is_load || head_is_store);

    // ------------------------------------------------------------------
    // CPU request outputs: held stable while valid && !ready.
    // ------------------------------------------------------------------
    assign cpu_req_valid = rst_n && launch;
    assign cpu_req_we    = match_is_store;
    assign cpu_req_addr  = eff_addr;
    assign cpu_req_wdata = store_wdata;
    assign cpu_req_be    = store_be;

    // ------------------------------------------------------------------
    // Load data extension
    // ------------------------------------------------------------------
    logic [31:0] load_ext;
    logic [7:0]  lb;
    logic [15:0] lh;
    assign lb = cpu_resp_rdata[8*launched_byte_off +: 8];
    assign lh = launched_byte_off[1] ? cpu_resp_rdata[31:16] : cpu_resp_rdata[15:0];

    always @* begin
        load_ext = cpu_resp_rdata;
        case (launched_funct3)
            3'b000: load_ext = {{24{lb[7]}}, lb};        // LB
            3'b001: load_ext = {{16{lh[15]}}, lh};       // LH
            3'b010: load_ext = cpu_resp_rdata;           // LW
            3'b100: load_ext = {24'b0, lb};              // LBU
            3'b101: load_ext = {16'b0, lh};              // LHU
            default: load_ext = cpu_resp_rdata;
        endcase
    end

    // ------------------------------------------------------------------
    // Response handling
    // ------------------------------------------------------------------
    logic resp_accept;
    assign resp_accept = cpu_resp_valid && launched_valid;

    assign lsu_complete_valid         = rst_n && resp_accept;
    assign lsu_complete_rob_id        = launched_rob_id;
    assign lsu_complete_pd            = launched_pd;
    assign lsu_complete_data          = launched_is_store ? 32'b0 : load_ext;
    assign lsu_complete_writes_rd     = (!launched_is_store) && launched_has_rd && (launched_pd != 6'b0);
    assign lsu_complete_is_store_done = launched_is_store;

    assign store_accept_valid   = rst_n && resp_accept && launched_is_store;
    assign store_accept_rob_id  = launched_rob_id;

    assign lsu_wb_valid = lsu_complete_valid && lsu_complete_writes_rd;
    assign lsu_wb_pd    = launched_pd;
    assign lsu_wb_data  = load_ext;

    // ------------------------------------------------------------------
    // load_waiting: head load presented cpu_req_valid, awaiting response.
    // ------------------------------------------------------------------
    assign load_waiting = rst_n && launched_valid && !launched_is_store
                          && !cpu_resp_valid;
    assign load_waiting_rob_id = launched_rob_id;

    // ------------------------------------------------------------------
    // Alloc ready: free entry available.
    // ------------------------------------------------------------------
    logic free_avail;
    logic free_idx;
    always @* begin
        free_avail = 1'b0;
        free_idx   = 1'b0;
        for (int i = 0; i < NENT; i = i + 1) begin
            if (!e_valid[i] && !free_avail) begin
                free_avail = 1'b1;
                free_idx   = i[0];
            end
        end
    end
    assign alloc_ready = rst_n && free_avail;

    // ------------------------------------------------------------------
    // Squash: drop entries younger than squash point (circular distance).
    // ------------------------------------------------------------------
    function automatic [3:0] rob_circular_dist(input logic [2:0] a, input logic [2:0] b);
        rob_circular_dist = (b - a) & 3'b111;
    endfunction

    logic [3:0] squash_tail_dist;
    assign squash_tail_dist = rob_circular_dist(squash_rob_head, squash_rob_tail);

    // ------------------------------------------------------------------
    // Sequential logic
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (!rst_n) begin
            for (int i = 0; i < NENT; i = i + 1) begin
                e_valid[i]    <= 1'b0;
                e_rob_id[i]   <= 3'b0;
                e_is_store[i] <= 1'b0;
                e_has_rd[i]   <= 1'b0;
                e_ps1[i]      <= 6'b0;
                e_ps2[i]      <= 6'b0;
                e_pd[i]       <= 6'b0;
                e_uses_rs2[i] <= 1'b0;
                e_imm[i]      <= 32'b0;
                e_funct3[i]   <= 3'b0;
                e_launched[i] <= 1'b0;
            end
            launched_valid    <= 1'b0;
            launched_rob_id   <= 3'b0;
            launched_pd       <= 6'b0;
            launched_is_store <= 1'b0;
            launched_has_rd   <= 1'b0;
            launched_funct3   <= 3'b0;
            launched_byte_off <= 2'b0;
        end else begin
            // Priority: squash > response > launch > alloc
            if (squash_valid) begin
                for (int i = 0; i < NENT; i = i + 1) begin
                    if (e_valid[i]) begin
                        if (rob_circular_dist(squash_rob_head, e_rob_id[i]) >= squash_tail_dist) begin
                            e_valid[i]    <= 1'b0;
                            e_launched[i] <= 1'b0;
                        end
                    end
                end
            end

            // Response: deallocate the launched entry (completion delivered this cycle)
            if (resp_accept) begin
                launched_valid <= 1'b0;
                for (int i = 0; i < NENT; i = i + 1) begin
                    if (e_valid[i] && (e_rob_id[i] == launched_rob_id)) begin
                        e_valid[i]    <= 1'b0;
                        e_launched[i] <= 1'b0;
                    end
                end
            end

            // Launch: mark matching entry launched and capture its context
            if (launch && cpu_req_ready) begin
                e_launched[match_idx] <= 1'b1;
                launched_valid    <= 1'b1;
                launched_rob_id   <= match_rob_id;
                launched_pd       <= match_pd;
                launched_is_store <= match_is_store;
                launched_has_rd   <= match_has_rd;
                launched_funct3   <= match_funct3;
                launched_byte_off <= byte_off;
            end

            // Alloc: push into free entry
            if (alloc_valid && alloc_ready) begin
                e_valid[free_idx]    <= 1'b1;
                e_rob_id[free_idx]   <= alloc_rob_id;
                e_is_store[free_idx] <= alloc_is_store;
                e_has_rd[free_idx]   <= alloc_has_rd;
                e_ps1[free_idx]      <= alloc_ps1;
                e_ps2[free_idx]      <= alloc_ps2;
                e_pd[free_idx]       <= alloc_pd;
                e_uses_rs2[free_idx] <= alloc_uses_rs2;
                e_imm[free_idx]      <= alloc_imm;
                e_funct3[free_idx]   <= alloc_funct3;
                e_launched[free_idx] <= 1'b0;
            end
        end
    end

endmodule
