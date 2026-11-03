module icache (
    input logic clk,
    input logic rst_n,
    input logic cpu_req_valid,
    input logic [31:0] cpu_req_addr,
    output logic cpu_req_ready,
    output logic cpu_resp_valid,
    output logic [31:0] cpu_resp_data,
    input logic flush,
    input logic [1:0] flush_epoch,
    output logic mem_req_valid,
    input logic mem_req_ready,
    output logic [31:0] mem_req_addr,
    input logic mem_resp_valid,
    input logic [31:0] mem_resp_data
);

    localparam IDLE   = 2'd0;
    localparam REFILL = 2'd1;
    localparam RESP   = 2'd2;

    logic [1:0] state;
    logic [31:0] resp_data_q;
    logic [1:0] req_epoch_q;

    logic [23:0] refill_tag;
    logic [3:0] refill_index;
    logic [1:0] refill_word;
    logic [1:0] refill_target_word;
    logic [1:0] refill_epoch_q;
    logic [31:0] refill_target_data;
    logic cancel_q;

    logic [31:0] data_array [0:15][0:3];
    logic [23:0] tag_array [0:15];
    logic valid_array [0:15];

    integer rst_i;

    wire [3:0] req_index = cpu_req_addr[7:4];
    wire [1:0] req_word = cpu_req_addr[3:2];
    wire [23:0] req_tag = cpu_req_addr[31:8];
    wire hit = valid_array[req_index] && (tag_array[req_index] == req_tag);
    wire [31:0] hit_data = data_array[req_index][req_word];

    assign cpu_req_ready = rst_n && (state == IDLE) && !flush;
    assign cpu_resp_valid = rst_n && (state == RESP) && !flush && (req_epoch_q == flush_epoch);
    assign cpu_resp_data = rst_n ? resp_data_q : 32'b0;

    assign mem_req_valid = rst_n && (state == REFILL);
    assign mem_req_addr = {refill_tag, refill_index, refill_word, 2'b00};

    always @(posedge clk) begin
        if (!rst_n) begin
            state <= IDLE;
            resp_data_q <= 32'b0;
            req_epoch_q <= 2'b0;
            refill_tag <= 24'b0;
            refill_index <= 4'b0;
            refill_word <= 2'b0;
            refill_target_word <= 2'b0;
            refill_epoch_q <= 2'b0;
            refill_target_data <= 32'b0;
            cancel_q <= 1'b0;
            for (rst_i = 0; rst_i < 16; rst_i = rst_i + 1) begin
                valid_array[rst_i] <= 1'b0;
            end
        end else begin
            case (state)
                IDLE: begin
                    if (flush)
                        cancel_q <= 1'b0;
                    if (cpu_req_valid && cpu_req_ready) begin
                        req_epoch_q <= flush_epoch;
                        if (hit) begin
                            resp_data_q <= hit_data;
                            state <= RESP;
                        end else begin
                            refill_tag <= cpu_req_addr[31:8];
                            refill_index <= cpu_req_addr[7:4];
                            refill_word <= 2'd0;
                            refill_target_word <= cpu_req_addr[3:2];
                            refill_epoch_q <= flush_epoch;
                            refill_target_data <= 32'b0;
                            cancel_q <= 1'b0;
                            valid_array[cpu_req_addr[7:4]] <= 1'b0;
                            state <= REFILL;
                        end
                    end
                end

                RESP: begin
                    if (flush || (req_epoch_q != flush_epoch))
                        state <= IDLE;
                    else
                        state <= IDLE;
                end

                REFILL: begin
                    if (!cancel_q && (flush || (refill_epoch_q != flush_epoch))) begin
                        if (mem_req_ready) begin
                            cancel_q <= 1'b0;
                            state <= IDLE;
                        end else begin
                            cancel_q <= 1'b1;
                        end
                    end else if (cancel_q) begin
                        if (mem_req_ready) begin
                            cancel_q <= 1'b0;
                            state <= IDLE;
                        end
                    end else begin
                        if (mem_req_valid && mem_req_ready) begin
                            data_array[refill_index][refill_word] <= mem_resp_data;
                            if (refill_word == refill_target_word) begin
                                refill_target_data <= mem_resp_data;
                            end
                            if (refill_word == 2'd3) begin
                                tag_array[refill_index] <= refill_tag;
                                valid_array[refill_index] <= 1'b1;
                                resp_data_q <= (refill_word == refill_target_word) ? mem_resp_data : refill_target_data;
                                req_epoch_q <= refill_epoch_q;
                                state <= RESP;
                            end else begin
                                refill_word <= refill_word + 2'd1;
                            end
                        end
                    end
                end

                default: begin
                    state <= IDLE;
                end
            endcase
        end
    end

endmodule
