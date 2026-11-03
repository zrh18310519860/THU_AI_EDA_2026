// branch_predictor: 16-entry direct-mapped BTB + 16-entry 2-bit saturating BHT.
// Prediction is combinational from pred_pc (index = pred_pc[5:2]).
// resolve_valid is a single-cycle pulse at ROB-head control resolution:
//   - BTB entry updated with valid/tag/target/type
//   - BHT saturating-updated only for conditional branches
// Priority: rst_n > resolve update > prediction read.
module branch_predictor (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        pred_req_valid,
    input  logic [31:0] pred_pc,
    output logic        pred_hit,
    output logic        pred_taken,
    output logic [31:0] pred_target,
    input  logic        resolve_valid,
    input  logic [31:0] resolve_pc,
    input  logic [31:0] resolve_next_pc,
    input  logic        resolve_is_branch,
    input  logic        resolve_is_jump,
    input  logic        resolve_taken,
    input  logic        resolve_mispredict
);

    logic        btb_valid [0:15];
    logic [31:0] btb_tag   [0:15];
    logic [31:0] btb_target[0:15];
    logic        btb_is_branch [0:15];
    logic        btb_is_jump   [0:15];
    logic [1:0]  bht [0:15];

    logic [3:0]  pred_idx;
    logic [3:0]  res_idx;
    logic        pred_btb_hit;
    logic        pred_is_jump;
    logic        pred_is_branch;
    logic [1:0]  pred_ctr;

    integer i;

    // Prediction read (combinational)
    always @* begin
        pred_idx      = pred_pc[5:2];
        pred_btb_hit  = btb_valid[pred_idx] && (btb_tag[pred_idx] == pred_pc);
        pred_is_jump  = btb_is_jump[pred_idx];
        pred_is_branch= btb_is_branch[pred_idx];
        pred_ctr      = bht[pred_idx];

        pred_hit      = pred_req_valid && pred_btb_hit;
        if (pred_btb_hit) begin
            if (pred_is_jump)
                pred_taken = 1'b1;
            else if (pred_is_branch)
                pred_taken = (pred_ctr >= 2'b10);
            else
                pred_taken = 1'b0;
            pred_target = btb_target[pred_idx];
        end else begin
            pred_taken  = 1'b0;
            pred_target = pred_pc + 32'd4;
        end
    end

    // Resolve update (synchronous)
    always @(posedge clk) begin
        if (!rst_n) begin
            for (i = 0; i < 16; i = i + 1) begin
                btb_valid[i]     <= 1'b0;
                btb_tag[i]       <= 32'd0;
                btb_target[i]    <= 32'd0;
                btb_is_branch[i] <= 1'b0;
                btb_is_jump[i]   <= 1'b0;
                bht[i]           <= 2'b01;
            end
        end else if (resolve_valid) begin
            res_idx = resolve_pc[5:2];
            if (resolve_is_branch || resolve_is_jump) begin
                btb_valid[res_idx]     <= 1'b1;
                btb_tag[res_idx]       <= resolve_pc;
                btb_target[res_idx]    <= resolve_next_pc;
                btb_is_branch[res_idx] <= resolve_is_branch;
                btb_is_jump[res_idx]   <= resolve_is_jump;
            end
            if (resolve_is_branch) begin
                if (resolve_taken) begin
                    if (bht[res_idx] != 2'b11)
                        bht[res_idx] <= bht[res_idx] + 2'b01;
                end else begin
                    if (bht[res_idx] != 2'b00)
                        bht[res_idx] <= bht[res_idx] - 2'b01;
                end
            end
        end
    end

endmodule
