module decode_unit (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        dec_valid,
    input  logic [31:0] dec_pc,
    input  logic [31:0] dec_insn,
    input  logic [31:0] dec_pred_next_pc,
    input  logic        dec_pred_valid,
    output logic [4:0]  dec_rs1,
    output logic [4:0]  dec_rs2,
    output logic [4:0]  dec_rd,
    output logic        dec_uses_rs1,
    output logic        dec_uses_rs2,
    output logic        dec_writes_rd,
    output logic        dec_is_branch,
    output logic        dec_is_jump,
    output logic        dec_is_ecall,
    output logic        dec_is_load,
    output logic        dec_is_store,
    output logic [31:0] dec_imm,
    output logic [2:0]  dec_funct3,
    input  logic        dec_ready
);

    // Opcode constants (RV32I)
    localparam [6:0] OP_R      = 7'b0110011;
    localparam [6:0] OP_IALU   = 7'b0010011;
    localparam [6:0] OP_LOAD   = 7'b0000011;
    localparam [6:0] OP_STORE  = 7'b0100011;
    localparam [6:0] OP_BRANCH = 7'b1100011;
    localparam [6:0] OP_JAL    = 7'b1101111;
    localparam [6:0] OP_JALR   = 7'b1100111;
    localparam [6:0] OP_LUI    = 7'b0110111;
    localparam [6:0] OP_AUIPC  = 7'b0010111;

    logic [6:0] opcode;
    logic [2:0] funct3;
    logic [4:0] rs1;
    logic [4:0] rs2;
    logic [4:0] rd;

    logic [31:0] imm_i;
    logic [31:0] imm_s;
    logic [31:0] imm_b;
    logic [31:0] imm_u;
    logic [31:0] imm_j;

    logic is_r;
    logic is_ialu;
    logic is_load;
    logic is_store;
    logic is_branch;
    logic is_jal;
    logic is_jalr;
    logic is_lui;
    logic is_auipc;
    logic is_ecall;
    logic is_jump;

    logic uses_rs1;
    logic uses_rs2;
    logic writes_rd;
    logic [31:0] imm_sel;

    assign opcode = dec_insn[6:0];
    assign funct3 = dec_insn[14:12];
    assign rs1    = dec_insn[19:15];
    assign rs2    = dec_insn[24:20];
    assign rd     = dec_insn[11:7];

    // Immediate formats (standard RV32I)
    assign imm_i = {{20{dec_insn[31]}}, dec_insn[31:20]};
    assign imm_s = {{20{dec_insn[31]}}, dec_insn[31:25], dec_insn[11:7]};
    assign imm_b = {{19{dec_insn[31]}}, dec_insn[31], dec_insn[7],
                    dec_insn[30:25], dec_insn[11:8], 1'b0};
    assign imm_u = {dec_insn[31:12], 12'b0};
    assign imm_j = {{11{dec_insn[31]}}, dec_insn[31], dec_insn[19:12],
                    dec_insn[20], dec_insn[30:21], 1'b0};

    // Classification
    assign is_r      = (opcode == OP_R);
    assign is_ialu   = (opcode == OP_IALU);
    assign is_load   = (opcode == OP_LOAD);
    assign is_store  = (opcode == OP_STORE);
    assign is_branch = (opcode == OP_BRANCH);
    assign is_jal    = (opcode == OP_JAL);
    assign is_jalr   = (opcode == OP_JALR);
    assign is_lui    = (opcode == OP_LUI);
    assign is_auipc  = (opcode == OP_AUIPC);
    assign is_ecall  = (dec_insn == 32'h00000073);
    assign is_jump   = is_jal | is_jalr;

    // Source usage: OP-IMM never uses rs2 as a dependency.
    assign uses_rs1 = is_r | is_ialu | is_branch | is_load | is_store | is_jalr;
    assign uses_rs2 = is_r | is_branch | is_store;

    // Destination usage: only real register writers with rd != 0.
    assign writes_rd = (is_r | is_ialu | is_load | is_jal | is_jalr |
                        is_lui | is_auipc) & (rd != 5'd0);

    // Immediate selection by opcode
    always @* begin
        if (is_ialu | is_load | is_jalr)
            imm_sel = imm_i;
        else if (is_store)
            imm_sel = imm_s;
        else if (is_branch)
            imm_sel = imm_b;
        else if (is_lui | is_auipc)
            imm_sel = imm_u;
        else if (is_jal)
            imm_sel = imm_j;
        else
            imm_sel = 32'b0;
    end

    // Combinational decode; reset clears all outputs.
    always @* begin
        if (!rst_n) begin
            dec_rs1        = 5'd0;
            dec_rs2        = 5'd0;
            dec_rd         = 5'd0;
            dec_uses_rs1   = 1'b0;
            dec_uses_rs2   = 1'b0;
            dec_writes_rd  = 1'b0;
            dec_is_branch  = 1'b0;
            dec_is_jump    = 1'b0;
            dec_is_ecall   = 1'b0;
            dec_is_load    = 1'b0;
            dec_is_store   = 1'b0;
            dec_imm        = 32'b0;
            dec_funct3     = 3'b0;
        end else if (dec_valid) begin
            dec_rs1        = rs1;
            dec_rs2        = rs2;
            dec_rd         = rd;
            dec_uses_rs1   = uses_rs1;
            dec_uses_rs2   = uses_rs2;
            dec_writes_rd  = writes_rd;
            dec_is_branch  = is_branch;
            dec_is_jump    = is_jump;
            dec_is_ecall   = is_ecall;
            dec_is_load    = is_load;
            dec_is_store   = is_store;
            dec_imm        = imm_sel;
            dec_funct3     = funct3;
        end else begin
            dec_rs1        = 5'd0;
            dec_rs2        = 5'd0;
            dec_rd         = 5'd0;
            dec_uses_rs1   = 1'b0;
            dec_uses_rs2   = 1'b0;
            dec_writes_rd  = 1'b0;
            dec_is_branch  = 1'b0;
            dec_is_jump    = 1'b0;
            dec_is_ecall   = 1'b0;
            dec_is_load    = 1'b0;
            dec_is_store   = 1'b0;
            dec_imm        = 32'b0;
            dec_funct3     = 3'b0;
        end
    end

endmodule
