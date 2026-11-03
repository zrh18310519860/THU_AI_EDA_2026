// icache.sv
// 256B direct-mapped I-cache: 16 lines x 16B (4 words), tag = addr[31:8], index = addr[7:4], word = addr[3:2].
// CPU side: accept on cpu_req_valid && cpu_req_ready rising edge; registered one-cycle cpu_resp_valid.
// External bus: same-cycle-read master. mem_req_valid held with stable mem_req_addr until mem_req_ready.
// mem_resp_valid is the gated accept pulse (mem_req_valid && mem_req_ready); mem_resp_data sampled same edge.
// flush_epoch captured at accept; mismatched refill responses are discarded and do not set line valid.
// flush cancels only not-yet-accepted requests; accepted refill words complete but remaining obsolete-epoch words are not issued.
// Priority: rst_n > flush/epoch > refill progress > new accept.

module icache (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        cpu_req_valid,
    input  logic [31:0] cpu_req_addr,
    output logic        cpu_req_ready,
    output logic        cpu_resp_valid,
    output logic [31:0] cpu_resp_data,
    input  logic        flush,
    input  logic [1:0]  flush_epoch,
    output logic        mem_req_valid,
    output logic [31:0] mem_req_addr,
    input  logic        mem_req_ready,
    input  logic        mem_resp_valid,
    input  logic [31:0] mem_resp_data
);

    // Cache arrays (unpacked word arrays for variable indexing).
    logic [15:0]  line_valid;
    logic [23:0]  line_tag   [0:15];
    logic [31:0]  line_data  [0:15][0:3];

    // Refill FSM state.
    logic         refill_active;
    logic [1:0]   refill_word;      // 0..3
    logic [3:0]   refill_index;
    logic [23:0]  refill_tag;
    logic [1:0]   refill_epoch;
    logic [31:0]  refill_base;      // line base address
    logic [31:0]  refill_buf [0:3];

    // Registered CPU response.
    logic         resp_valid_r;
    logic [31:0]  resp_data_r;

    // Pending CPU request (accepted, awaiting hit response or miss refill).
    logic         pend_valid;
    logic [31:0]  pend_addr;
    logic [1:0]   pend_epoch;

    // Combinational lookup for the pending request.
    logic [3:0]   pend_index;
    logic [23:0]  pend_tag;
    logic [1:0]   pend_word;
    logic         pend_hit;
    logic [31:0]  pend_hit_data;

    // Combinational lookup for a new incoming request.
    logic [3:0]   new_index;
    logic [23:0]  new_tag;
    logic [1:0]   new_word;
    logic         new_hit;
    logic [31:0]  new_hit_data;

    assign pend_index = pend_addr[7:4];
    assign pend_tag   = pend_addr[31:8];
    assign pend_word  = pend_addr[3:2];
    assign pend_hit   = line_valid[pend_index] && (line_tag[pend_index] == pend_tag);
    assign pend_hit_data = line_data[pend_index][pend_word];

    assign new_index = cpu_req_addr[7:4];
    assign new_tag   = cpu_req_addr[31:8];
    assign new_word  = cpu_req_addr[3:2];
    assign new_hit   = line_valid[new_index] && (line_tag[new_index] == new_tag);
    assign new_hit_data = line_data[new_index][new_word];

    // External bus request: driven by refill FSM.
    assign mem_req_valid = refill_active;
    assign mem_req_addr  = refill_base + {28'd0, refill_word, 2'b00};

    // CPU ready: accept only when idle (no pending, no refill, no response this cycle).
    assign cpu_req_ready = rst_n && !refill_active && !pend_valid && !resp_valid_r;

    // CPU response outputs.
    assign cpu_resp_valid = resp_valid_r;
    assign cpu_resp_data  = resp_data_r;

    // Bus accept pulse (same-cycle read).
    logic mem_accept;
    assign mem_accept = mem_resp_valid;

    integer i;

    always @(posedge clk) begin
        if (!rst_n) begin
            line_valid     <= 16'd0;
            refill_active  <= 1'b0;
            refill_word    <= 2'd0;
            refill_index   <= 4'd0;
            refill_tag     <= 24'd0;
            refill_epoch   <= 2'd0;
            refill_base    <= 32'd0;
            resp_valid_r   <= 1'b0;
            resp_data_r    <= 32'd0;
            pend_valid     <= 1'b0;
            pend_addr      <= 32'd0;
            pend_epoch     <= 2'd0;
            for (i = 0; i < 4; i = i + 1) begin
                refill_buf[i] <= 32'd0;
            end
        end else begin
            // Default: clear one-cycle response pulse.
            resp_valid_r <= 1'b0;

            // ---- Refill progress (highest priority below reset) ----
            if (refill_active) begin
                if (mem_accept) begin
                    if (refill_epoch != flush_epoch) begin
                        // Obsolete epoch: discard accepted word, stop refill.
                        refill_active <= 1'b0;
                        refill_word   <= 2'd0;
                    end else begin
                        // Sample returned word.
                        refill_buf[refill_word] <= mem_resp_data;
                        if (refill_word == 2'd3) begin
                            // Last word: install line.
                            line_valid[refill_index] <= 1'b1;
                            line_tag[refill_index]   <= refill_tag;
                            line_data[refill_index][0] <= refill_buf[0];
                            line_data[refill_index][1] <= refill_buf[1];
                            line_data[refill_index][2] <= refill_buf[2];
                            line_data[refill_index][3] <= mem_resp_data;
                            // Return the registered CPU response for the
                            // originally accepted miss request.
                            resp_valid_r <= 1'b1;
                            if (pend_word == 2'd3)
                                resp_data_r <= mem_resp_data;
                            else
                                resp_data_r <= refill_buf[pend_word];
                            refill_active <= 1'b0;
                            refill_word   <= 2'd0;
                        end else begin
                            refill_word <= refill_word + 2'd1;
                        end
                    end
                end
            end

            // ---- Pending request resolution ----
            if (pend_valid) begin
                if (pend_hit) begin
                    resp_valid_r <= 1'b1;
                    resp_data_r  <= pend_hit_data;
                    pend_valid   <= 1'b0;
                end else if (!refill_active) begin
                    // Start refill for the pending miss.
                    refill_active <= 1'b1;
                    refill_word   <= 2'd0;
                    refill_index  <= pend_index;
                    refill_tag    <= pend_tag;
                    refill_epoch  <= pend_epoch;
                    refill_base   <= {pend_addr[31:4], 4'b0000};
                    pend_valid    <= 1'b0;
                end
            end

            // ---- New CPU accept ----
            if (cpu_req_valid && cpu_req_ready) begin
                if (new_hit) begin
                    resp_valid_r <= 1'b1;
                    resp_data_r  <= new_hit_data;
                end else begin
                    pend_valid <= 1'b1;
                    pend_addr  <= cpu_req_addr;
                    pend_epoch <= flush_epoch;
                end
            end
        end
    end

endmodule
