module branch_predictor (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        bp_lookup_valid,
    input  logic [31:0] bp_lookup_pc,
    output logic        bp_pred_valid,
    output logic        bp_pred_taken,
    output logic [31:0] bp_pred_target,
    output logic [1:0]  bp_pred_type,
    input  logic        bp_update_valid,
    input  logic [31:0] bp_update_pc,
    input  logic        bp_update_actual_taken,
    input  logic [31:0] bp_update_actual_target,
    input  logic        bp_update_is_branch,
    input  logic        bp_update_is_jal,
    input  logic        bp_update_is_jalr,
    input  logic [31:0] bp_update_pred_next_pc,
    input  logic        bp_update_pred_valid,
    output logic        bp_resolve_pulse,
    output logic        bp_mispredict_pulse,
    output logic [31:0] bp_mispredict_target
);

    logic [15:0] btb_valid;
    logic [31:0] btb_tag    [0:15];
    logic [31:0] btb_target [0:15];
    logic [1:0]  btb_type   [0:15];
    logic [1:0]  bht        [0:15];

    logic [31:0] lookup_next_pc;
    logic [31:0] update_next_pc;
    logic [31:0] update_pred_pc;
    logic        update_is_ctrl;
    integer      idx;

    assign lookup_next_pc = bp_lookup_pc + 32'd4;
    assign update_pred_pc = bp_update_pred_valid ? bp_update_pred_next_pc
                                                 : (bp_update_pc + 32'd4);
    assign update_next_pc = (bp_update_is_jal || bp_update_is_jalr)
                            ? bp_update_actual_target
                            : (bp_update_actual_taken ? bp_update_actual_target
                                                      : (bp_update_pc + 32'd4));
    assign update_is_ctrl = bp_update_is_branch || bp_update_is_jal || bp_update_is_jalr;

    assign bp_resolve_pulse    = bp_update_valid && update_is_ctrl;
    assign bp_mispredict_pulse = bp_update_valid && update_is_ctrl &&
                                 (update_next_pc != update_pred_pc);
    assign bp_mispredict_target = update_next_pc;

    always @* begin
        bp_pred_valid  = 1'b0;
        bp_pred_taken  = 1'b0;
        bp_pred_target = lookup_next_pc;
        bp_pred_type   = 2'b00;
        if (bp_lookup_valid) begin
            if (btb_valid[bp_lookup_pc[5:2]] &&
                (btb_tag[bp_lookup_pc[5:2]] == bp_lookup_pc)) begin
                bp_pred_valid = 1'b1;
                bp_pred_type  = btb_type[bp_lookup_pc[5:2]];
                if (btb_type[bp_lookup_pc[5:2]] == 2'b01) begin
                    if (bht[bp_lookup_pc[5:2]][1]) begin
                        bp_pred_taken  = 1'b1;
                        bp_pred_target = btb_target[bp_lookup_pc[5:2]];
                    end else begin
                        bp_pred_taken  = 1'b0;
                        bp_pred_target = lookup_next_pc;
                    end
                end else if ((btb_type[bp_lookup_pc[5:2]] == 2'b10) ||
                             (btb_type[bp_lookup_pc[5:2]] == 2'b11)) begin
                    bp_pred_taken  = 1'b1;
                    bp_pred_target = btb_target[bp_lookup_pc[5:2]];
                end else begin
                    bp_pred_valid  = 1'b0;
                    bp_pred_type   = 2'b00;
                    bp_pred_taken  = 1'b0;
                    bp_pred_target = lookup_next_pc;
                end
            end
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            btb_valid <= 16'd0;
            for (idx = 0; idx < 16; idx = idx + 1) begin
                bht[idx] <= 2'b01;
            end
        end else if (bp_update_valid && update_is_ctrl) begin
            if (bp_update_is_branch) begin
                btb_valid[bp_update_pc[5:2]]  <= 1'b1;
                btb_tag[bp_update_pc[5:2]]    <= bp_update_pc;
                btb_target[bp_update_pc[5:2]] <= bp_update_actual_target;
                btb_type[bp_update_pc[5:2]]   <= 2'b01;
                case (bht[bp_update_pc[5:2]])
                    2'b00: begin
                        bht[bp_update_pc[5:2]] <= bp_update_actual_taken ? 2'b01 : 2'b00;
                    end
                    2'b01: begin
                        bht[bp_update_pc[5:2]] <= bp_update_actual_taken ? 2'b10 : 2'b00;
                    end
                    2'b10: begin
                        bht[bp_update_pc[5:2]] <= bp_update_actual_taken ? 2'b11 : 2'b01;
                    end
                    2'b11: begin
                        bht[bp_update_pc[5:2]] <= bp_update_actual_taken ? 2'b11 : 2'b10;
                    end
                    default: begin
                        bht[bp_update_pc[5:2]] <= 2'b01;
                    end
                endcase
            end else if (bp_update_is_jal) begin
                btb_valid[bp_update_pc[5:2]]  <= 1'b1;
                btb_tag[bp_update_pc[5:2]]    <= bp_update_pc;
                btb_target[bp_update_pc[5:2]] <= bp_update_actual_target;
                btb_type[bp_update_pc[5:2]]   <= 2'b10;
            end else if (bp_update_is_jalr) begin
                btb_valid[bp_update_pc[5:2]]  <= 1'b1;
                btb_tag[bp_update_pc[5:2]]    <= bp_update_pc;
                btb_target[bp_update_pc[5:2]] <= bp_update_actual_target;
                btb_type[bp_update_pc[5:2]]   <= 2'b11;
            end
        end
    end

endmodule
