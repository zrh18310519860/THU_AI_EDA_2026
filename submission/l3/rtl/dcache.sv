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
    output logic        mem_req_we,
    output logic [31:0] mem_req_addr,
    output logic [31:0] mem_req_wdata,
    output logic [3:0]  mem_req_be,
    input  logic        mem_req_ready,
    input  logic        mem_resp_valid,
    input  logic [31:0] mem_resp_data
);

    // ------------------------------------------------------------------
    // 256B direct-mapped D-cache: 16 lines x 16B (4 words).
    // index = addr[7:4], word = addr[3:2], tag = addr[31:8].
    // Write-through + write-allocate. Byte-lane merge on store.
    // ------------------------------------------------------------------

    localparam integer NLINES = 16;
    localparam integer NWORDS = 4;

    // FSM states
    localparam [2:0] S_IDLE     = 3'd0;
    localparam [2:0] S_REFILL   = 3'd1;
    localparam [2:0] S_WT       = 3'd2;
    localparam [2:0] S_RESP     = 3'd3;

    // Cache storage (unpacked word arrays for variable indexing)
    logic [31:0] data_arr [0:NLINES-1][0:NWORDS-1];
    logic [23:0] tag_arr  [0:NLINES-1];
    logic        valid_arr[0:NLINES-1];

    // FSM / control registers
    logic [2:0]  state;
    logic [1:0]  refill_cnt;

    // Latched request
    logic        req_we;
    logic [31:0] req_addr;
    logic [31:0] req_wdata;
    logic [3:0]  req_be;
    logic [1:0]  req_epoch;

    // Refill context
    logic [31:0] refill_base;   // line base (word aligned)
    logic [23:0] refill_tag;
    logic [3:0]  refill_idx;

    // Write-through context
    logic [31:0] wt_addr;
    logic [31:0] wt_wdata;
    logic [3:0]  wt_be;

    // Response register
    logic        resp_valid_r;
    logic [31:0] resp_rdata_r;

    // ------------------------------------------------------------------
    // Combinational decode of the latched request
    // ------------------------------------------------------------------
    logic [3:0]  req_idx;
    logic [1:0]  req_wsel;
    logic [23:0] req_tag;
    logic        req_hit;
    logic [31:0] req_line_word;
    logic [31:0] req_merged_word;

    assign req_idx  = req_addr[7:4];
    assign req_wsel = req_addr[3:2];
    assign req_tag  = req_addr[31:8];

    assign req_hit  = valid_arr[req_idx] && (tag_arr[req_idx] == req_tag);
    assign req_line_word = data_arr[req_idx][req_wsel];

    // Accept-cycle decode from the live CPU request (registered req_* are stale
    // during the S_IDLE accept edge).
    logic [3:0]  acc_idx;
    logic [1:0]  acc_wsel;
    logic [23:0] acc_tag;
    logic        acc_hit;
    logic [31:0] acc_line_word;
    logic [31:0] acc_merged_word;

    assign acc_idx  = cpu_req_addr[7:4];
    assign acc_wsel = cpu_req_addr[3:2];
    assign acc_tag  = cpu_req_addr[31:8];
    assign acc_hit  = valid_arr[acc_idx] && (tag_arr[acc_idx] == acc_tag);
    assign acc_line_word = data_arr[acc_idx][acc_wsel];

    always @* begin
        acc_merged_word = acc_line_word;
        if (cpu_req_be[0]) acc_merged_word[7:0]   = cpu_req_wdata[7:0];
        if (cpu_req_be[1]) acc_merged_word[15:8]  = cpu_req_wdata[15:8];
        if (cpu_req_be[2]) acc_merged_word[23:16] = cpu_req_wdata[23:16];
        if (cpu_req_be[3]) acc_merged_word[31:24] = cpu_req_wdata[31:24];
    end

    // Byte-lane merge for store
    always @* begin
        req_merged_word = req_line_word;
        if (req_be[0]) req_merged_word[7:0]   = req_wdata[7:0];
        if (req_be[1]) req_merged_word[15:8]  = req_wdata[15:8];
        if (req_be[2]) req_merged_word[23:16] = req_wdata[23:16];
        if (req_be[3]) req_merged_word[31:24] = req_wdata[31:24];
    end

    // Byte-lane merge against an explicit base word (used when the store's
    // word slot is the last refilled word, whose line data is not yet stored).
    function automatic [31:0] merge_be(input logic [31:0] base_w,
                                       input logic [31:0] wdata,
                                       input logic [3:0]  be);
        logic [31:0] m;
        begin
            m = base_w;
            if (be[0]) m[7:0]   = wdata[7:0];
            if (be[1]) m[15:8]  = wdata[15:8];
            if (be[2]) m[23:16] = wdata[23:16];
            if (be[3]) m[31:24] = wdata[31:24];
            merge_be = m;
        end
    endfunction

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    assign cpu_req_ready = rst_n && (state == S_IDLE);

    assign cpu_resp_valid = rst_n && resp_valid_r;
    assign cpu_resp_rdata = resp_rdata_r;

    assign mem_req_valid = rst_n && ((state == S_REFILL) || (state == S_WT));
    assign mem_req_we    = (state == S_WT);
    assign mem_req_addr  = (state == S_WT) ? wt_addr : (refill_base + {refill_cnt, 2'b00});
    assign mem_req_wdata = (state == S_WT) ? wt_wdata : 32'b0;
    assign mem_req_be    = (state == S_WT) ? wt_be : 4'b1111;

    // ------------------------------------------------------------------
    // Sequential logic
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (!rst_n) begin
            state        <= S_IDLE;
            refill_cnt   <= 2'd0;
            req_we       <= 1'b0;
            req_addr     <= 32'b0;
            req_wdata    <= 32'b0;
            req_be       <= 4'b0;
            req_epoch    <= 2'b0;
            refill_base  <= 32'b0;
            refill_tag   <= 24'b0;
            refill_idx   <= 4'b0;
            wt_addr      <= 32'b0;
            wt_wdata     <= 32'b0;
            wt_be        <= 4'b0;
            resp_valid_r <= 1'b0;
            resp_rdata_r <= 32'b0;
            for (int i = 0; i < NLINES; i = i + 1) begin
                valid_arr[i] <= 1'b0;
            end
        end else begin
            // Default: clear response pulse
            resp_valid_r <= 1'b0;

            // Flush: cancel not-yet-accepted requests (only in IDLE)
            if (flush && (state == S_IDLE)) begin
                // nothing pending to cancel in IDLE
            end

            case (state)
                S_IDLE: begin
                    if (cpu_req_valid && cpu_req_ready) begin
                        // Latch request
                        req_we    <= cpu_req_we;
                        req_addr  <= cpu_req_addr;
                        req_wdata <= cpu_req_wdata;
                        req_be    <= cpu_req_be;
                        req_epoch <= flush_epoch;

                        if (cpu_req_we) begin
                            // Store: hit -> merge + write-through; miss -> refill first
                            if (acc_hit) begin
                                data_arr[acc_idx][acc_wsel] <= acc_merged_word;
                                wt_addr  <= cpu_req_addr;
                                wt_wdata <= cpu_req_wdata;
                                wt_be    <= cpu_req_be;
                                state    <= S_WT;
                            end else begin
                                refill_base <= {cpu_req_addr[31:4], 4'b0000};
                                refill_tag  <= cpu_req_addr[31:8];
                                refill_idx  <= cpu_req_addr[7:4];
                                refill_cnt  <= 2'd0;
                                state       <= S_REFILL;
                            end
                        end else begin
                            // Load: hit -> respond next cycle; miss -> refill
                            if (acc_hit) begin
                                resp_rdata_r <= acc_line_word;
                                resp_valid_r <= 1'b1;
                                state        <= S_IDLE;
                            end else begin
                                refill_base <= {cpu_req_addr[31:4], 4'b0000};
                                refill_tag  <= cpu_req_addr[31:8];
                                refill_idx  <= cpu_req_addr[7:4];
                                refill_cnt  <= 2'd0;
                                state       <= S_REFILL;
                            end
                        end
                    end
                end

                S_REFILL: begin
                    if (mem_req_valid && mem_req_ready) begin
                        // Install word
                        data_arr[refill_idx][refill_cnt] <= mem_resp_data;
                        if (refill_cnt == 2'd3) begin
                            // Line complete
                            tag_arr[refill_idx]   <= refill_tag;
                            valid_arr[refill_idx] <= 1'b1;
                            refill_cnt            <= 2'd0;

                            if (req_we) begin
                                // Store miss: merge store into freshly refilled line.
                                // When the store's word slot is the last refilled word,
                                // req_merged_word was computed from stale line data, so
                                // merge against the just-installed refill word instead.
                                if (req_wsel == refill_cnt) begin
                                    data_arr[refill_idx][req_wsel] <= merge_be(mem_resp_data, req_wdata, req_be);
                                end else begin
                                    data_arr[refill_idx][req_wsel] <= req_merged_word;
                                end
                                wt_addr  <= req_addr;
                                wt_wdata <= req_wdata;
                                wt_be    <= req_be;
                                state    <= S_WT;
                            end else begin
                                // Load miss: respond with refilled word
                                resp_rdata_r <= (req_wsel == refill_cnt) ? mem_resp_data
                                                                         : data_arr[refill_idx][req_wsel];
                                resp_valid_r <= 1'b1;
                                state        <= S_IDLE;
                            end
                        end else begin
                            refill_cnt <= refill_cnt + 2'd1;
                        end
                    end
                end

                S_WT: begin
                    if (mem_req_valid && mem_req_ready) begin
                        // Write-through accepted -> respond next cycle
                        resp_rdata_r <= 32'b0;
                        resp_valid_r <= 1'b1;
                        state        <= S_IDLE;
                    end
                end

                S_RESP: begin
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end

endmodule
