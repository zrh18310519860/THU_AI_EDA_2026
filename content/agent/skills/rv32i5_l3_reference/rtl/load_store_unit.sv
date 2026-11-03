module load_store_unit (
    input logic clk,
    input logic rst_n,
    input logic halted,
    input logic issue_valid,
    output logic issue_ready,
    input logic [31:0] issue_pc,
    input logic [31:0] issue_insn,
    input logic [15:0] issue_ctrl,
    input logic [2:0] issue_funct3,
    input logic [31:0] issue_imm,
    input logic [31:0] issue_src1,
    input logic [31:0] issue_src2,
    input logic [5:0] issue_prd,
    input logic [3:0] issue_rob_id,
    input logic [3:0] rob_head_id,
    input logic squash_valid,
    input logic [3:0] squash_rob_id,
    output logic dcache_cpu_req_valid,
    output logic dcache_cpu_req_we,
    output logic [31:0] dcache_cpu_req_addr,
    output logic [31:0] dcache_cpu_req_wdata,
    output logic [3:0] dcache_cpu_req_be,
    input logic dcache_cpu_req_ready,
    input logic dcache_cpu_resp_valid,
    input logic [31:0] dcache_cpu_resp_rdata,
    input logic dcache_mem_waiting,
    output logic complete_valid,
    output logic [3:0] complete_rob_id,
    output logic [5:0] complete_prd,
    output logic [31:0] complete_data,
    output logic complete_we,
    output logic lsu_load_waiting
);

    logic [1:0] count;
    logic head_ptr;

    logic ent_valid [0:1];
    logic ent_launched [0:1];
    logic ent_is_store [0:1];
    logic [3:0] ent_rob_id [0:1];
    logic [5:0] ent_prd [0:1];
    logic [31:0] ent_addr [0:1];
    logic [31:0] ent_wdata [0:1];
    logic [3:0] ent_be [0:1];
    logic [2:0] ent_funct3 [0:1];

    logic [31:0] issue_addr_comb;
    logic [31:0] issue_wdata_comb;
    logic [3:0] issue_be_comb;

    logic head_valid;
    logic head_is_store;
    logic head_is_launched;
    logic [1:0] head_off;
    logic [2:0] head_funct3;
    logic [5:0] head_prd;
    logic [3:0] head_rob_id;
    logic [31:0] head_wdata;
    logic [3:0] head_be;

    logic [7:0] byte_sel;
    logic [15:0] half_sel;
    logic [31:0] load_data_comb;

    logic [3:0] age_s;
    logic [3:0] age0;
    logic [3:0] age1;
    logic sq0;
    logic sq1;
    logic sq_h;
    logic sq_t;

    logic complete_now;
    logic accept;
    logic launch_accept;
    logic [1:0] alloc_idx;

    always @* begin
        issue_addr_comb = issue_src1 + issue_imm;
        issue_wdata_comb = 32'h0;
        issue_be_comb = 4'h0;
        case (issue_funct3)
            3'b000: begin
                issue_be_comb = 4'b0001 << issue_addr_comb[1:0];
                issue_wdata_comb = {4{issue_src2[7:0]}};
            end
            3'b001: begin
                issue_be_comb = issue_addr_comb[1] ? 4'b1100 : 4'b0011;
                issue_wdata_comb = {2{issue_src2[15:0]}};
            end
            3'b010: begin
                issue_be_comb = 4'b1111;
                issue_wdata_comb = issue_src2;
            end
            default: begin
                issue_be_comb = 4'h0;
                issue_wdata_comb = 32'h0;
            end
        endcase
    end

    always @* begin
        head_valid = ent_valid[head_ptr];
        head_is_store = ent_is_store[head_ptr];
        head_is_launched = ent_launched[head_ptr];
        head_off = 2'b00;
        head_funct3 = 3'b010;
        head_prd = 6'd0;
        head_rob_id = 4'd0;
        head_wdata = 32'h0;
        head_be = 4'h0;
        if (head_ptr) begin
            head_off = ent_addr[1][1:0];
            head_funct3 = ent_funct3[1];
            head_prd = ent_prd[1];
            head_rob_id = ent_rob_id[1];
            head_wdata = ent_wdata[1];
            head_be = ent_be[1];
        end else begin
            head_off = ent_addr[0][1:0];
            head_funct3 = ent_funct3[0];
            head_prd = ent_prd[0];
            head_rob_id = ent_rob_id[0];
            head_wdata = ent_wdata[0];
            head_be = ent_be[0];
        end
    end

    always @* begin
        age_s = (squash_rob_id - rob_head_id) & 4'h7;
        age0 = (ent_rob_id[0] - rob_head_id) & 4'h7;
        age1 = (ent_rob_id[1] - rob_head_id) & 4'h7;
        sq0 = ent_valid[0] && squash_valid && (age0 > age_s);
        sq1 = ent_valid[1] && squash_valid && (age1 > age_s);
        if (head_ptr) begin
            sq_h = sq1;
            sq_t = sq0;
        end else begin
            sq_h = sq0;
            sq_t = sq1;
        end
    end

    always @* begin
        byte_sel = 8'h0;
        half_sel = 16'h0;
        case (head_off)
            2'b00: byte_sel = dcache_cpu_resp_rdata[7:0];
            2'b01: byte_sel = dcache_cpu_resp_rdata[15:8];
            2'b10: byte_sel = dcache_cpu_resp_rdata[23:16];
            2'b11: byte_sel = dcache_cpu_resp_rdata[31:24];
            default: byte_sel = 8'h0;
        endcase
        half_sel = head_off[1] ? dcache_cpu_resp_rdata[31:16] : dcache_cpu_resp_rdata[15:0];
        case (head_funct3)
            3'b000: load_data_comb = {{24{byte_sel[7]}}, byte_sel};
            3'b001: load_data_comb = {{16{half_sel[15]}}, half_sel};
            3'b010: load_data_comb = dcache_cpu_resp_rdata;
            3'b100: load_data_comb = {24'b0, byte_sel};
            3'b101: load_data_comb = {16'b0, half_sel};
            default: load_data_comb = dcache_cpu_resp_rdata;
        endcase
    end

    always @* begin
        complete_now = rst_n && !halted && dcache_cpu_resp_valid &&
                       head_valid && head_is_launched && !sq_h;

        accept = issue_valid && issue_ready;

        alloc_idx = 2'd0;
        if (head_ptr ^ count[0]) begin
            alloc_idx = 2'd1;
        end else begin
            alloc_idx = 2'd0;
        end

        if (rst_n && !halted && head_valid && !head_is_launched && !sq_h) begin
            dcache_cpu_req_valid = 1'b1;
            dcache_cpu_req_we = head_is_store;
            dcache_cpu_req_addr = head_ptr ? ent_addr[1] : ent_addr[0];
            dcache_cpu_req_wdata = head_is_store ? head_wdata : 32'h0;
            dcache_cpu_req_be = head_is_store ? head_be : 4'h0;
        end else begin
            dcache_cpu_req_valid = 1'b0;
            dcache_cpu_req_we = 1'b0;
            dcache_cpu_req_addr = head_ptr ? ent_addr[1] : ent_addr[0];
            dcache_cpu_req_wdata = head_wdata;
            dcache_cpu_req_be = head_be;
        end

        launch_accept = dcache_cpu_req_valid && dcache_cpu_req_ready;

        issue_ready = rst_n && !halted && !squash_valid &&
                      ((count != 2'd2) || complete_now);

        complete_valid = complete_now;
        complete_rob_id = complete_now ? head_rob_id : 4'd0;
        complete_prd = complete_now ? head_prd : 6'd0;
        complete_data = complete_now ? (head_is_store ? 32'h0 : load_data_comb) : 32'h0;
        complete_we = complete_now && !head_is_store && (head_prd != 6'd0);

        lsu_load_waiting = rst_n && !halted && dcache_mem_waiting &&
                           head_valid && !head_is_store && head_is_launched && !sq_h;
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            count <= 2'd0;
            head_ptr <= 1'b0;
            ent_valid[0] <= 1'b0;
            ent_valid[1] <= 1'b0;
            ent_launched[0] <= 1'b0;
            ent_launched[1] <= 1'b0;
            ent_is_store[0] <= 1'b0;
            ent_is_store[1] <= 1'b0;
            ent_rob_id[0] <= 4'd0;
            ent_rob_id[1] <= 4'd0;
            ent_prd[0] <= 6'd0;
            ent_prd[1] <= 6'd0;
            ent_addr[0] <= 32'h0;
            ent_addr[1] <= 32'h0;
            ent_wdata[0] <= 32'h0;
            ent_wdata[1] <= 32'h0;
            ent_be[0] <= 4'h0;
            ent_be[1] <= 4'h0;
            ent_funct3[0] <= 3'b010;
            ent_funct3[1] <= 3'b010;
        end else begin
            if (sq_h) begin
                ent_valid[0] <= 1'b0;
                ent_valid[1] <= 1'b0;
                ent_launched[0] <= 1'b0;
                ent_launched[1] <= 1'b0;
                count <= 2'd0;
                head_ptr <= 1'b0;
            end else if (sq_t && (count == 2'd2)) begin
                if (head_ptr) begin
                    ent_valid[0] <= 1'b0;
                    ent_launched[0] <= 1'b0;
                end else begin
                    ent_valid[1] <= 1'b0;
                    ent_launched[1] <= 1'b0;
                end
                count <= 2'd1;
            end else begin
                if (complete_now) begin
                    if (head_ptr) begin
                        ent_valid[1] <= 1'b0;
                        ent_launched[1] <= 1'b0;
                    end else begin
                        ent_valid[0] <= 1'b0;
                        ent_launched[0] <= 1'b0;
                    end
                end

                if (launch_accept) begin
                    if (head_ptr) begin
                        ent_launched[1] <= 1'b1;
                    end else begin
                        ent_launched[0] <= 1'b1;
                    end
                end

                if (accept) begin
                    if (alloc_idx == 2'd0) begin
                        ent_valid[0] <= 1'b1;
                        ent_launched[0] <= 1'b0;
                        ent_is_store[0] <= issue_ctrl[4];
                        ent_rob_id[0] <= issue_rob_id;
                        ent_prd[0] <= issue_prd;
                        ent_addr[0] <= issue_addr_comb;
                        ent_wdata[0] <= issue_wdata_comb;
                        ent_be[0] <= issue_be_comb;
                        ent_funct3[0] <= issue_funct3;
                    end else begin
                        ent_valid[1] <= 1'b1;
                        ent_launched[1] <= 1'b0;
                        ent_is_store[1] <= issue_ctrl[4];
                        ent_rob_id[1] <= issue_rob_id;
                        ent_prd[1] <= issue_prd;
                        ent_addr[1] <= issue_addr_comb;
                        ent_wdata[1] <= issue_wdata_comb;
                        ent_be[1] <= issue_be_comb;
                        ent_funct3[1] <= issue_funct3;
                    end
                end

                if (complete_now && accept) begin
                    count <= count;
                    head_ptr <= ~head_ptr;
                end else if (complete_now) begin
                    count <= count - 2'd1;
                    head_ptr <= ~head_ptr;
                end else if (accept) begin
                    count <= count + 2'd1;
                end
            end
        end
    end

endmodule
