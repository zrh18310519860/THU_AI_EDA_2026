module frontend_unit (
    input  logic        clk,
    input  logic        rst_n,
    output logic        icache_cpu_req_valid,
    output logic [31:0] icache_cpu_req_addr,
    input  logic        icache_cpu_req_ready,
    input  logic        icache_cpu_resp_valid,
    input  logic [31:0] icache_cpu_resp_data,
    output logic        bp_lookup_valid,
    output logic [31:0] bp_lookup_pc,
    input  logic        bp_pred_valid,
    input  logic        bp_pred_taken,
    input  logic [31:0] bp_pred_target,
    input  logic [1:0]  bp_pred_type,
    input  logic        redirect_valid,
    input  logic [31:0] redirect_pc,
    input  logic [1:0]  redirect_epoch,
    input  logic        stop_fetch,
    output logic        fetch_valid,
    input  logic        fetch_ready,
    output logic [31:0] fetch_pc,
    output logic [31:0] fetch_insn,
    output logic [31:0] fetch_pred_next_pc,
    output logic        fetch_pred_valid,
    output logic [1:0]  fetch_pred_type
);

    localparam [1:0] S_REQ   = 2'd0;
    localparam [1:0] S_RESP  = 2'd1;
    localparam [1:0] S_VALID = 2'd2;
    localparam [1:0] S_DRAIN = 2'd3;

    logic [1:0]  state;
    logic [31:0] pc;
    logic [31:0] f_pc;
    logic [31:0] f_insn;
    logic [31:0] f_pred_next;
    logic        f_pred_valid;
    logic [1:0]  f_pred_type;

    function automatic [31:0] calc_pred_next;
        input [31:0] base_pc;
        input        pv;
        input        pt;
        input [31:0] ptgt;
        input [1:0]  ptype;
        begin
            if (pv && ((ptype == 2'b10) || (ptype == 2'b11)))
                calc_pred_next = {ptgt[31:2], 2'b00};
            else if (pv && (ptype == 2'b01) && pt)
                calc_pred_next = {ptgt[31:2], 2'b00};
            else
                calc_pred_next = {base_pc[31:2], 2'b00} + 32'd4;
        end
    endfunction

    assign icache_cpu_req_valid = (state == S_REQ) && !stop_fetch && !redirect_valid;
    assign icache_cpu_req_addr  = pc;

    assign bp_lookup_valid = ((state == S_REQ) && icache_cpu_req_valid) || (state == S_RESP);
    assign bp_lookup_pc    = pc;

    assign fetch_valid         = (state == S_VALID) && !stop_fetch && !redirect_valid;
    assign fetch_pc            = f_pc;
    assign fetch_insn          = f_insn;
    assign fetch_pred_next_pc  = f_pred_next;
    assign fetch_pred_valid    = f_pred_valid;
    assign fetch_pred_type     = f_pred_type;

    always @(posedge clk) begin
        if (!rst_n) begin
            state        <= S_REQ;
            pc           <= 32'd0;
            f_pc         <= 32'd0;
            f_insn       <= 32'd0;
            f_pred_next  <= 32'd4;
            f_pred_valid <= 1'b0;
            f_pred_type  <= 2'b00;
        end else begin
            if (redirect_valid) begin
                pc    <= {redirect_pc[31:2], 2'b00};
                state <= S_REQ;
            end else begin
                case (state)
                    S_REQ: begin
                        if (icache_cpu_req_valid && icache_cpu_req_ready)
                            state <= S_RESP;
                    end
                    S_RESP: begin
                        if (icache_cpu_resp_valid) begin
                            f_pc         <= pc;
                            f_insn       <= icache_cpu_resp_data;
                            f_pred_next  <= calc_pred_next(pc, bp_pred_valid, bp_pred_taken, bp_pred_target, bp_pred_type);
                            f_pred_valid <= bp_pred_valid;
                            f_pred_type  <= bp_pred_type;
                            state        <= S_VALID;
                        end
                    end
                    S_VALID: begin
                        if (fetch_ready && !stop_fetch) begin
                            pc    <= f_pred_next;
                            state <= S_REQ;
                        end
                    end
                    S_DRAIN: begin
                        state <= S_REQ;
                    end
                    default: begin
                        state <= S_REQ;
                    end
                endcase
            end
        end
    end

endmodule
