// fetch_frontend: PC/fetch frontend for the L3 out-of-order core.
// Drives the I-cache CPU request port, consumes epoch-tagged responses,
// queries the branch predictor combinationally for the fetched PC, and
// presents decoded-instruction handoff to the decode/rename stage.
//
// Protocol notes for downstream integration:
//  * cpu_req_valid is asserted without waiting for cpu_req_ready; cpu_req_addr
//    is held stable while cpu_req_valid && !cpu_req_ready.
//  * On cpu_resp_valid the returned word is latched together with the PC that
//    was requested; dec_valid is held until dec_ready accepts it.
//  * pred_req_valid/pred_pc are combinational for the currently fetched PC.
//  * dec_pred_next_pc = pred_target when pred_taken else dec_pc+4.
//  * PC update priority: rst_n > redirect_valid > stop_fetch/halted freeze >
//    predicted next PC (on dec accept) > PC+4.
//  * stop_fetch or halted prevents new cpu_req_valid and clears dec_valid.
module fetch_frontend (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        redirect_valid,
    input  logic [31:0] redirect_pc,
    input  logic        stop_fetch,
    input  logic        halted,
    output logic        cpu_req_valid,
    output logic [31:0] cpu_req_addr,
    input  logic        cpu_req_ready,
    input  logic        cpu_resp_valid,
    input  logic [31:0] cpu_resp_data,
    output logic        pred_req_valid,
    output logic [31:0] pred_pc,
    input  logic        pred_hit,
    input  logic        pred_taken,
    input  logic [31:0] pred_target,
    output logic        dec_valid,
    output logic [31:0] dec_pc,
    output logic [31:0] dec_insn,
    output logic [31:0] dec_pred_next_pc,
    output logic        dec_pred_valid,
    input  logic        dec_ready
);

    // Fetch PC register.
    logic [31:0] pc_q;

    // Outstanding I-cache request tracking.
    logic        req_outstanding_q;
    logic [31:0] req_addr_q;
    logic [31:0] req_pred_next_q;

    // Decode handoff registers.
    logic        dec_valid_q;
    logic [31:0] dec_pc_q;
    logic [31:0] dec_insn_q;
    logic [31:0] dec_pred_next_q;
    logic        dec_pred_valid_q;

    // Freeze condition: no new fetch when stopping or halted.
    logic        fetch_frozen;

    // Combinational prediction for the current fetch PC.
    logic        pred_taken_c;
    logic [31:0] pred_next_c;

    // Request acceptance this cycle.
    logic        req_accept;

    // Decode acceptance this cycle.
    logic        dec_accept;

    // ------------------------------------------------------------------
    // Combinational prediction for the PC being fetched.
    // ------------------------------------------------------------------
    always @* begin
        pred_taken_c = pred_taken;
        if (pred_taken)
            pred_next_c = pred_target;
        else
            pred_next_c = pc_q + 32'd4;
    end

    // ------------------------------------------------------------------
    // Output drivers.
    // ------------------------------------------------------------------
    always @* begin
        fetch_frozen = stop_fetch | halted;

        // I-cache request: issue when not frozen, no request outstanding, and
        // no decoded instruction is pending (prevents response overwrite).
        cpu_req_valid = rst_n & ~fetch_frozen & ~redirect_valid & ~req_outstanding_q & ~dec_valid_q;
        cpu_req_addr  = pc_q;

        // Predictor query for the PC being fetched.
        pred_req_valid = rst_n & ~fetch_frozen & ~redirect_valid & ~req_outstanding_q & ~dec_valid_q;
        pred_pc        = pc_q;

        // Decode handoff.
        dec_valid        = rst_n & dec_valid_q;
        dec_pc           = dec_pc_q;
        dec_insn         = dec_insn_q;
        dec_pred_next_pc = dec_pred_next_q;
        dec_pred_valid   = dec_pred_valid_q;
    end

    // ------------------------------------------------------------------
    // Handshake qualification.
    // ------------------------------------------------------------------
    always @* begin
        req_accept = cpu_req_valid & cpu_req_ready;
        dec_accept = dec_valid & dec_ready;
    end

    // ------------------------------------------------------------------
    // Sequential state.
    // ------------------------------------------------------------------
    always @(posedge clk) begin
        if (!rst_n) begin
            pc_q             <= 32'd0;
            req_outstanding_q<= 1'b0;
            req_addr_q       <= 32'd0;
            req_pred_next_q  <= 32'd0;
            dec_valid_q      <= 1'b0;
            dec_pc_q         <= 32'd0;
            dec_insn_q       <= 32'd0;
            dec_pred_next_q  <= 32'd0;
            dec_pred_valid_q <= 1'b0;
        end else begin
            // ---- Outstanding request tracking ----
            if (req_accept) begin
                req_outstanding_q <= 1'b1;
                req_addr_q        <= pc_q;
                req_pred_next_q   <= pred_next_c;
            end else if (cpu_resp_valid) begin
                req_outstanding_q <= 1'b0;
            end

            // ---- Decode handoff ----
            // A response produces a new decode entry unless one is already
            // pending; a pending entry is cleared on accept.
            if (cpu_resp_valid && req_outstanding_q && !redirect_valid) begin
                dec_valid_q      <= 1'b1;
                dec_pc_q         <= req_addr_q;
                dec_insn_q       <= cpu_resp_data;
                dec_pred_valid_q <= 1'b1;
                // Prediction metadata for the fetched PC. The predictor is
                // queried for req_addr_q; since the request was issued for
                // that PC, the combinational prediction corresponds to it.
                dec_pred_next_q  <= req_pred_next_q;
            end else if (dec_accept) begin
                dec_valid_q      <= 1'b0;
                dec_pred_valid_q <= 1'b0;
            end

            // ---- PC update ----
            if (redirect_valid) begin
                pc_q <= redirect_pc;
                req_outstanding_q <= 1'b0;
                dec_valid_q       <= 1'b0;
                dec_pred_valid_q  <= 1'b0;
            end else if (fetch_frozen) begin
                pc_q <= pc_q;
            end else if (dec_accept) begin
                pc_q <= dec_pred_next_q;
            end else if (req_accept) begin
                // Advance to predicted next PC once the request is accepted.
                pc_q <= pred_next_c;
            end
        end
    end

endmodule
