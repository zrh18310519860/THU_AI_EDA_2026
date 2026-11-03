module decode_unit (
    input  logic        fetch_valid,
    input  logic [31:0] fetch_pc,
    input  logic [31:0] fetch_insn,
    input  logic [31:0] fetch_pred_next_pc,
    input  logic        fetch_pred_valid,
    input  logic [1:0]  fetch_pred_type,
    input  logic        uop_ready,
    output logic        fetch_ready,
    output logic        uop_valid,
    output logic [31:0] uop_pc,
    output logic [31:0] uop_insn,
    output logic [4:0]  uop_rs1,
    output logic [4:0]  uop_rs2,
    output logic [4:0]  uop_rd,
    output logic [31:0] uop_imm,
    output logic [2:0]  uop_funct3,
    output logic [4:0]  uop_alu_op,
    output logic [15:0] uop_ctrl,
    output logic [31:0] uop_pred_next_pc,
    output logic        uop_pred_valid,
    output logic [1:0]  uop_pred_type
);

    logic [15:0] ctrl_d;
    logic [31:0] imm_d;
    logic [4:0]  alu_d;
    logic [31:0] pred_pc_d;
    logic        pred_valid_d;
    logic [1:0]  pred_type_d;
    logic        is_ctrl_d;

    always @* begin
        ctrl_d       = 16'd0;
        imm_d        = 32'd0;
        alu_d        = 5'd0;
        pred_pc_d    = fetch_pc + 32'd4;
        pred_valid_d = 1'b0;
        pred_type_d  = 2'b00;
        is_ctrl_d    = 1'b0;

        case (fetch_insn[6:0])
            7'b0110011: begin
                ctrl_d[0]  = 1'b1;
                ctrl_d[1]  = 1'b1;
                ctrl_d[2]  = 1'b1;
                ctrl_d[11] = 1'b1;
                case (fetch_insn[14:12])
                    3'b000: alu_d = fetch_insn[30] ? 5'd1 : 5'd0;
                    3'b001: alu_d = 5'd2;
                    3'b010: alu_d = 5'd3;
                    3'b011: alu_d = 5'd4;
                    3'b100: alu_d = 5'd5;
                    3'b101: alu_d = fetch_insn[30] ? 5'd7 : 5'd6;
                    3'b110: alu_d = 5'd8;
                    3'b111: alu_d = 5'd9;
                    default: alu_d = 5'd0;
                endcase
            end
            7'b0010011: begin
                ctrl_d[0]  = 1'b1;
                ctrl_d[2]  = 1'b1;
                ctrl_d[11] = 1'b1;
                imm_d = {{20{fetch_insn[31]}}, fetch_insn[31:20]};
                case (fetch_insn[14:12])
                    3'b000: alu_d = 5'd0;
                    3'b001: alu_d = 5'd2;
                    3'b010: alu_d = 5'd3;
                    3'b011: alu_d = 5'd4;
                    3'b100: alu_d = 5'd5;
                    3'b101: alu_d = fetch_insn[30] ? 5'd7 : 5'd6;
                    3'b110: alu_d = 5'd8;
                    3'b111: alu_d = 5'd9;
                    default: alu_d = 5'd0;
                endcase
            end
            7'b0110111: begin
                ctrl_d[2] = 1'b1;
                ctrl_d[9] = 1'b1;
                imm_d = {fetch_insn[31:12], 12'b0};
            end
            7'b0010111: begin
                ctrl_d[2]  = 1'b1;
                ctrl_d[10] = 1'b1;
                imm_d = {fetch_insn[31:12], 12'b0};
            end
            7'b0000011: begin
                ctrl_d[0]  = 1'b1;
                ctrl_d[2]  = 1'b1;
                ctrl_d[3]  = 1'b1;
                ctrl_d[12] = fetch_insn[14];
                imm_d = {{20{fetch_insn[31]}}, fetch_insn[31:20]};
                alu_d = 5'd0;
            end
            7'b0100011: begin
                ctrl_d[0] = 1'b1;
                ctrl_d[1] = 1'b1;
                ctrl_d[4] = 1'b1;
                imm_d = {{20{fetch_insn[31]}}, fetch_insn[31:25], fetch_insn[11:7]};
                alu_d = 5'd0;
            end
            7'b1100011: begin
                ctrl_d[0]  = 1'b1;
                ctrl_d[1]  = 1'b1;
                ctrl_d[5]  = 1'b1;
                ctrl_d[13] = 1'b1;
                imm_d = {{20{fetch_insn[31]}}, fetch_insn[7], fetch_insn[30:25], fetch_insn[11:8], 1'b0};
                is_ctrl_d = 1'b1;
                alu_d = 5'd0;
            end
            7'b1101111: begin
                ctrl_d[2]  = 1'b1;
                ctrl_d[6]  = 1'b1;
                ctrl_d[13] = 1'b1;
                imm_d = {{12{fetch_insn[31]}}, fetch_insn[19:12], fetch_insn[20], fetch_insn[30:21], 1'b0};
                is_ctrl_d = 1'b1;
            end
            7'b1100111: begin
                ctrl_d[0]  = 1'b1;
                ctrl_d[2]  = 1'b1;
                ctrl_d[7]  = 1'b1;
                ctrl_d[13] = 1'b1;
                imm_d = {{20{fetch_insn[31]}}, fetch_insn[31:20]};
                is_ctrl_d = 1'b1;
            end
            7'b1110011: begin
                if (fetch_insn == 32'h0000_0073) begin
                    ctrl_d[8] = 1'b1;
                end
            end
            default: begin
                ctrl_d = 16'd0;
            end
        endcase

        if (is_ctrl_d && fetch_pred_valid) begin
            pred_pc_d    = fetch_pred_next_pc;
            pred_valid_d = 1'b1;
            pred_type_d  = fetch_pred_type;
        end else begin
            pred_pc_d    = fetch_pc + 32'd4;
            pred_valid_d = 1'b0;
            pred_type_d  = 2'b00;
        end

        fetch_ready      = uop_ready;
        uop_valid        = fetch_valid;
        uop_pc           = fetch_pc;
        uop_insn         = fetch_insn;
        uop_rs1          = fetch_insn[19:15];
        uop_rs2          = fetch_insn[24:20];
        uop_rd           = fetch_insn[11:7];
        uop_imm          = imm_d;
        uop_funct3       = fetch_insn[14:12];
        uop_alu_op       = alu_d;
        uop_ctrl         = ctrl_d;
        uop_pred_next_pc = pred_pc_d;
        uop_pred_valid   = pred_valid_d;
        uop_pred_type    = pred_type_d;
    end

endmodule
