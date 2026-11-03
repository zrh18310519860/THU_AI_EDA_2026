module integer_execute_unit (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        issue_valid,
    output logic        issue_ready,
    input  logic [2:0]  issue_rob_id,
    input  logic [31:0] issue_pc,
    input  logic [31:0] issue_insn,
    input  logic [31:0] issue_rs1_val,
    input  logic [31:0] issue_rs2_val,
    input  logic [5:0]  issue_pd,
    input  logic        issue_writes_rd,
    input  logic        issue_is_branch,
    input  logic        issue_is_jump,
    input  logic [31:0] issue_pred_next_pc,
    input  logic        issue_pred_valid,
    output logic        int_complete_valid,
    output logic [2:0]  int_complete_rob_id,
    output logic [5:0]  int_complete_pd,
    output logic [31:0] int_complete_data,
    output logic [31:0] int_complete_actual_next_pc,
    output logic        int_complete_taken,
    output logic        int_wb_valid,
    output logic [5:0]  int_wb_pd,
    output logic [31:0] int_wb_data
);

    // Opcode constants
    localparam [6:0] OP_R      = 7'b0110011;
    localparam [6:0] OP_IALU   = 7'b0010011;
    localparam [6:0] OP_LOAD   = 7'b0000011;
    localparam [6:0] OP_STORE  = 7'b0100011;
    localparam [6:0] OP_BRANCH = 7'b1100011;
    localparam [6:0] OP_JAL    = 7'b1101111;
    localparam [6:0] OP_JALR   = 7'b1100111;
    localparam [6:0] OP_LUI    = 7'b0110111;
    localparam [6:0] OP_AUIPC  = 7'b0010111;

    // Decode fields from issue_insn
    logic [6:0]  opcode;
    logic [2:0]  funct3;
    logic [6:0]  funct7;
    logic [31:0] imm_i;
    logic [31:0] imm_s;
    logic [31:0] imm_b;
    logic [31:0] imm_u;
    logic [31:0] imm_j;

    assign opcode = issue_insn[6:0];
    assign funct3 = issue_insn[14:12];
    assign funct7 = issue_insn[31:25];

    assign imm_i = {{20{issue_insn[31]}}, issue_insn[31:20]};
    assign imm_s = {{20{issue_insn[31]}}, issue_insn[31:25], issue_insn[11:7]};
    assign imm_b = {{19{issue_insn[31]}}, issue_insn[31], issue_insn[7],
                    issue_insn[30:25], issue_insn[11:8], 1'b0};
    assign imm_u = {issue_insn[31:12], 12'b0};
    assign imm_j = {{11{issue_insn[31]}}, issue_insn[31], issue_insn[19:12],
                    issue_insn[20], issue_insn[30:21], 1'b0};

    // ALU operand selection: OP-IMM never uses rs2 as a dependency.
    logic [31:0] alu_a;
    logic [31:0] alu_b;
    logic [31:0] alu_result;

    always @* begin
        alu_a = issue_rs1_val;
        alu_b = issue_rs2_val;
        if (opcode == OP_IALU) begin
            alu_b = imm_i;
        end else if (opcode == OP_LUI) begin
            alu_a = 32'b0;
            alu_b = imm_u;
        end else if (opcode == OP_AUIPC) begin
            alu_a = issue_pc;
            alu_b = imm_u;
        end
    end

    // Shift amount
    logic [4:0] shamt;
    always @* begin
        if (opcode == OP_IALU)
            shamt = issue_insn[24:20];
        else
            shamt = issue_rs2_val[4:0];
    end

    // ALU computation
    always @* begin
        alu_result = 32'b0;
        case (opcode)
            OP_R, OP_IALU: begin
                case (funct3)
                    3'b000: begin
                        if (opcode == OP_R && funct7[5])
                            alu_result = alu_a - alu_b;
                        else
                            alu_result = alu_a + alu_b;
                    end
                    3'b001: alu_result = alu_a << shamt;
                    3'b010: alu_result = ($signed(alu_a) < $signed(alu_b)) ? 32'd1 : 32'd0;
                    3'b011: alu_result = (alu_a < alu_b) ? 32'd1 : 32'd0;
                    3'b100: alu_result = alu_a ^ alu_b;
                    3'b101: begin
                        if (funct7[5])
                            alu_result = $signed(alu_a) >>> shamt;
                        else
                            alu_result = alu_a >> shamt;
                    end
                    3'b110: alu_result = alu_a | alu_b;
                    3'b111: alu_result = alu_a & alu_b;
                    default: alu_result = 32'b0;
                endcase
            end
            OP_LUI:   alu_result = imm_u;
            OP_AUIPC: alu_result = issue_pc + imm_u;
            default:  alu_result = 32'b0;
        endcase
    end

    // Branch condition
    logic branch_taken;
    always @* begin
        branch_taken = 1'b0;
        if (issue_is_branch) begin
            case (funct3)
                3'b000: branch_taken = (issue_rs1_val == issue_rs2_val);
                3'b001: branch_taken = (issue_rs1_val != issue_rs2_val);
                3'b100: branch_taken = ($signed(issue_rs1_val) < $signed(issue_rs2_val));
                3'b101: branch_taken = ($signed(issue_rs1_val) >= $signed(issue_rs2_val));
                3'b110: branch_taken = (issue_rs1_val < issue_rs2_val);
                3'b111: branch_taken = (issue_rs1_val >= issue_rs2_val);
                default: branch_taken = 1'b0;
            endcase
        end
    end

    // Control-transfer target and taken
    logic [31:0] branch_target;
    logic [31:0] jal_target;
    logic [31:0] jalr_target;
    logic [31:0] actual_next_pc;
    logic        actual_taken;

    assign branch_target = issue_pc + imm_b;
    assign jal_target    = issue_pc + imm_j;
    assign jalr_target   = (issue_rs1_val + imm_i) & 32'hFFFF_FFFE;

    always @* begin
        actual_next_pc = issue_pc + 32'd4;
        actual_taken   = 1'b0;
        if (issue_is_branch) begin
            if (branch_taken) begin
                actual_next_pc = branch_target;
                actual_taken   = 1'b1;
            end else begin
                actual_next_pc = issue_pc + 32'd4;
                actual_taken   = 1'b0;
            end
        end else if (issue_is_jump) begin
            if (opcode == OP_JALR)
                actual_next_pc = jalr_target;
            else
                actual_next_pc = jal_target;
            actual_taken = 1'b1;
        end
    end

    // Completion data: ALU result, LUI, AUIPC, or link pc+4 for jumps.
    logic [31:0] complete_data;
    always @* begin
        if (issue_is_jump)
            complete_data = issue_pc + 32'd4;
        else
            complete_data = alu_result;
    end

    // issue_ready is always 1 (single-cycle execute, no backpressure).
    assign issue_ready = 1'b1;

    // Registered completion / writeback pulses.
    always @(posedge clk) begin
        if (!rst_n) begin
            int_complete_valid       <= 1'b0;
            int_complete_rob_id      <= 3'b0;
            int_complete_pd          <= 6'b0;
            int_complete_data        <= 32'b0;
            int_complete_actual_next_pc <= 32'b0;
            int_complete_taken       <= 1'b0;
            int_wb_valid             <= 1'b0;
            int_wb_pd                <= 6'b0;
            int_wb_data              <= 32'b0;
        end else begin
            int_complete_valid <= 1'b0;
            int_wb_valid       <= 1'b0;
            if (issue_valid && issue_ready) begin
                int_complete_valid          <= 1'b1;
                int_complete_rob_id         <= issue_rob_id;
                int_complete_pd             <= issue_pd;
                int_complete_data           <= complete_data;
                int_complete_actual_next_pc <= actual_next_pc;
                int_complete_taken          <= actual_taken;
                if (issue_writes_rd && (issue_pd != 6'b0)) begin
                    int_wb_valid <= 1'b1;
                    int_wb_pd    <= issue_pd;
                    int_wb_data  <= complete_data;
                end
            end
        end
    end

endmodule
