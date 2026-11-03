module dcache (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        cpu_req_valid,
    input  logic        cpu_req_we,
    input  logic [31:0] cpu_req_addr,
    input  logic [31:0] cpu_req_wdata,
    input  logic [3:0]  cpu_req_be,
    output logic        cpu_req_ready,
    output logic        cpu_resp_valid,
    output logic [31:0] cpu_resp_rdata,
    input  logic        flush,
    input  logic [1:0]  flush_epoch,
    output logic        mem_req_valid,
    input  logic        mem_req_ready,
    output logic [31:0] mem_req_addr,
    output logic        mem_req_we,
    output logic [31:0] mem_req_wdata,
    output logic [3:0]  mem_req_be,
    input  logic        mem_resp_valid,
    input  logic [31:0] mem_resp_data,
    output logic        mem_waiting
);

    localparam ST_IDLE   = 0;
    localparam ST_REFILL = 1;
    localparam ST_WT     = 2;
    localparam ST_RESP   = 3;

    logic [1:0] state;

    logic        line_valid [0:15];
    logic [23:0] line_tag   [0:15];
    logic [31:0] line_data  [0:15][0:3];

    logic [31:0] refill_buf [0:3];

    logic        op_we;
    logic [31:0] op_addr;
    logic [31:0] op_wdata;
    logic [3:0]  op_be;
    logic [1:0]  op_epoch;
    logic [3:0]  op_index;
    logic [1:0]  op_word;
    logic [23:0] op_tag;

    logic [31:0] refill_base;
    logic [1:0]  refill_word;
    logic        resp_use_buf;

    wire mem_accept;

    function automatic [31:0] merge_word;
        input [31:0] oldw;
        input [31:0] neww;
        input [3:0]  be;
        begin
            merge_word = oldw;
            if (be[0]) merge_word[7:0]   = neww[7:0];
            if (be[1]) merge_word[15:8]  = neww[15:8];
            if (be[2]) merge_word[23:16] = neww[23:16];
            if (be[3]) merge_word[31:24] = neww[31:24];
        end
    endfunction

    assign mem_accept = mem_req_valid && mem_req_ready && mem_resp_valid;

    assign cpu_req_ready = rst_n && (state == ST_IDLE) && !flush;

    assign cpu_resp_valid = rst_n && (state == ST_RESP);

    assign cpu_resp_rdata = (rst_n && (state == ST_RESP) && !op_we) ?
                            (resp_use_buf ? refill_buf[op_word] : line_data[op_index][op_word]) :
                            32'b0;

    assign mem_req_valid = rst_n && ((state == ST_REFILL) || (state == ST_WT));

    assign mem_req_addr = (state == ST_REFILL) ? {refill_base[31:4], refill_word, 2'b00} :
                          (state == ST_WT)     ? op_addr :
                                                 32'b0;

    assign mem_req_we = rst_n && (state == ST_WT);

    assign mem_req_wdata = (state == ST_WT) ? op_wdata : 32'b0;

    assign mem_req_be = (state == ST_WT) ? op_be : 4'b0000;

    assign mem_waiting = rst_n && ((state == ST_REFILL) || (state == ST_WT));

    integer i;

    always @(posedge clk) begin
        if (!rst_n) begin
            state <= ST_IDLE;

            for (i = 0; i < 16; i = i + 1) begin
                line_valid[i] <= 1'b0;
            end

            op_we       <= 1'b0;
            op_addr     <= 32'b0;
            op_wdata    <= 32'b0;
            op_be       <= 4'b0;
            op_epoch    <= 2'b0;
            op_index    <= 4'b0;
            op_word     <= 2'b0;
            op_tag      <= 24'b0;
            refill_base <= 32'b0;
            refill_word <= 2'b0;
            resp_use_buf <= 1'b0;
        end else begin
            case (state)
                ST_IDLE: begin
                    if (cpu_req_valid && !flush) begin
                        op_we    <= cpu_req_we;
                        op_addr  <= cpu_req_addr;
                        op_wdata <= cpu_req_wdata;
                        op_be    <= cpu_req_be;
                        op_epoch <= flush_epoch;
                        op_index <= cpu_req_addr[7:4];
                        op_word  <= cpu_req_addr[3:2];
                        op_tag   <= cpu_req_addr[31:8];

                        if (cpu_req_we) begin
                            if (line_valid[cpu_req_addr[7:4]] &&
                                (line_tag[cpu_req_addr[7:4]] == cpu_req_addr[31:8])) begin
                                line_data[cpu_req_addr[7:4]][cpu_req_addr[3:2]] <=
                                    merge_word(line_data[cpu_req_addr[7:4]][cpu_req_addr[3:2]],
                                               cpu_req_wdata, cpu_req_be);
                                state <= ST_WT;
                            end else begin
                                line_valid[cpu_req_addr[7:4]] <= 1'b0;
                                refill_base <= {cpu_req_addr[31:4], 4'b0};
                                refill_word <= 2'b0;
                                state <= ST_REFILL;
                            end
                        end else begin
                            if (line_valid[cpu_req_addr[7:4]] &&
                                (line_tag[cpu_req_addr[7:4]] == cpu_req_addr[31:8])) begin
                                resp_use_buf <= 1'b0;
                                state <= ST_RESP;
                            end else begin
                                line_valid[cpu_req_addr[7:4]] <= 1'b0;
                                refill_base <= {cpu_req_addr[31:4], 4'b0};
                                refill_word <= 2'b0;
                                resp_use_buf <= 1'b1;
                                state <= ST_REFILL;
                            end
                        end
                    end
                end

                ST_REFILL: begin
                    if (mem_accept) begin
                        refill_buf[refill_word] <= mem_resp_data;

                        if (refill_word == 2'd3) begin
                            if (op_epoch == flush_epoch) begin
                                line_valid[op_index] <= 1'b1;
                                line_tag[op_index]   <= op_tag;

                                line_data[op_index][0] <=
                                    (op_we && (op_word == 2'd0)) ?
                                    merge_word(refill_buf[0], op_wdata, op_be) : refill_buf[0];

                                line_data[op_index][1] <=
                                    (op_we && (op_word == 2'd1)) ?
                                    merge_word(refill_buf[1], op_wdata, op_be) : refill_buf[1];

                                line_data[op_index][2] <=
                                    (op_we && (op_word == 2'd2)) ?
                                    merge_word(refill_buf[2], op_wdata, op_be) : refill_buf[2];

                                line_data[op_index][3] <=
                                    (op_we && (op_word == 2'd3)) ?
                                    merge_word(mem_resp_data, op_wdata, op_be) : mem_resp_data;
                            end

                            if (op_we) begin
                                state <= ST_WT;
                            end else begin
                                state <= ST_RESP;
                            end
                        end else begin
                            refill_word <= refill_word + 2'd1;
                        end
                    end
                end

                ST_WT: begin
                    if (mem_accept) begin
                        resp_use_buf <= 1'b0;
                        state <= ST_RESP;
                    end
                end

                ST_RESP: begin
                    state <= ST_IDLE;
                end

                default: begin
                    state <= ST_IDLE;
                end
            endcase
        end
    end

endmodule
