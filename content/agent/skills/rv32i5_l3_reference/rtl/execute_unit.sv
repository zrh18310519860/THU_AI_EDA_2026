module execute_unit (
    input  logic        ex_valid,
    input  logic [31:0] ex_pc,
    input  logic [31:0] ex_insn,
    input  logic [15:0] ex_ctrl,
    input  logic [2:0]  ex_funct3,
    input  logic [4:0]  ex_alu_op,
    input  logic [31:0] ex_imm,
    input  logic [31:0] ex_src1,
    input  logic [31:0] ex_src2,
    input  logic [31:0] ex_pred_next_pc,
    input  logic        ex_pred_valid,
    output logic [31:0] ex_result,
    output logic [31:0] ex_actual_next_pc,
    output logic        ex_branch_taken,
    output logic        ex_is_control,
    output logic        ex_mispredict,
    output logic        ex_redirect_valid,
    output logic [31:0] ex_redirect_pc,
    output logic [31:0] ex_complete_data
);

    logic [31:0] alu_a;
    logic [31:0] alu_b;
    logic [31:0] alu_out;
    logic [31:0] link_pc;
    logic [31:0] predicted_next_pc;
    logic [31:0] branch_target;
    logic [31:0] jump_target;
    logic [31:0] mem_addr;
    logic [4:0]  shamt;
    logic        branch_cond;

    always @* begin
        alu_a = ex_src1;
        alu_b = ex_ctrl[1] ? ex_src2 : ex_imm;
        alu_out = 32'd0;
        link_pc = ex_pc + 32'd4;
        predicted_next_pc = ex_pred_valid ? ex_pred_next_pc : (ex_pc + 32'd4);
        branch_target = ex_pc + ex_imm;
        jump_target = (ex_src1 + ex_imm) & 32'hFFFF_FFFE;
        mem_addr = ex_src1 + ex_imm;
        shamt = alu_b[4:0];
        branch_cond = 1'b0;

        if (ex_ctrl[5]) begin
            case (ex_funct3)
                3'b000: branch_cond = (ex_src1 == ex_src2);
                3'b001: branch_cond = (ex_src1 != ex_src2);
                3'b100: branch_cond = ($signed(ex_src1) < $signed(ex_src2));
                3'b101: branch_cond = ($signed(ex_src1) >= $signed(ex_src2));
                3'b110: branch_cond = (ex_src1 < ex_src2);
                3'b111: branch_cond = (ex_src1 >= ex_src2);
                default: branch_cond = 1'b0;
            endcase
        end

        case (ex_alu_op)
            5'd0: alu_out = alu_a + alu_b;
            5'd1: alu_out = alu_a - alu_b;
            5'd2: alu_out = alu_a << shamt;
            5'd3: alu_out = ($signed(alu_a) < $signed(alu_b)) ? 32'd1 : 32'd0;
            5'd4: alu_out = (alu_a < alu_b) ? 32'd1 : 32'd0;
            5'd5: alu_out = alu_a ^ alu_b;
            5'd6: alu_out = alu_a >> shamt;
            5'd7: alu_out = $signed(alu_a) >>> shamt;
            5'd8: alu_out = alu_a | alu_b;
            5'd9: alu_out = alu_a & alu_b;
            default: alu_out = 32'd0;
        endcase

        ex_result = 32'd0;
        if (ex_ctrl[9]) begin
            ex_result = ex_imm;
        end else if (ex_ctrl[10]) begin
            ex_result = ex_pc + ex_imm;
        end else if (ex_ctrl[6] || ex_ctrl[7]) begin
            ex_result = link_pc;
        end else if (ex_ctrl[3] || ex_ctrl[4]) begin
            ex_result = mem_addr;
        end else begin
            ex_result = alu_out;
        end

        ex_complete_data = ex_result;

        ex_actual_next_pc = ex_pc + 32'd4;
        if (ex_ctrl[5]) begin
            ex_actual_next_pc = branch_cond ? branch_target : (ex_pc + 32'd4);
        end else if (ex_ctrl[6]) begin
            ex_actual_next_pc = ex_pc + ex_imm;
        end else if (ex_ctrl[7]) begin
            ex_actual_next_pc = jump_target;
        end

        ex_branch_taken = ex_valid && ((ex_ctrl[5] && branch_cond) || ex_ctrl[6] || ex_ctrl[7]);
        ex_is_control = ex_valid && ex_ctrl[13];
        ex_mispredict = ex_valid && ex_ctrl[13] && (ex_actual_next_pc != predicted_next_pc);
        ex_redirect_valid = ex_mispredict;
        ex_redirect_pc = ex_actual_next_pc;
    end

endmodule
