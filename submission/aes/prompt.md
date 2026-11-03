Implement a single synthesizable SystemVerilog module aes128 for FIPS-197 AES-128 encryption, prioritizing low mapped area and switching. Output only complete RTL, no Markdown, explanation, or testbench. No other modules, includes, external files, initial blocks, delays or test-vector special cases.

Exact interface:
module aes128(input logic clk, input logic rst_n, input logic start,
 input logic [127:0] key, input logic [127:0] plaintext,
 output logic ready, output logic done, output logic [127:0] ciphertext);

Use synchronous active-low reset in always @(posedge clk). Reset cancels any job, done=0, ready=1. Accept only start while idle, latch both inputs then. Ignore start while busy. done is exactly one cycle, ready=0 during done. On the following edge return to idle (ready=1, done=0); allow a new start on this return edge as well. Ciphertext is stable throughout done. No async reset. Maximum latency 200 cycles, clock period 10 ns.

Architecture is fixed to TWO shared forward S-boxes total, time-multiplexed between state and key expansion. Current state and current key are 128-bit each, no eleven-key table. Use a round counter 1..10, phase IDLE/KEY_A/KEY_B/SUB/ROUND/DONE, SUB counter 0..7, and 16-bit g_hi:
1. Start edge: state_reg=plaintext XOR key, round_key=key, rcon=1, round=1; enter KEY_A.
2. KEY_A: S-box input0=round_key[23:16], input1=round_key[15:8]. Store g_hi={S0,S1}; enter KEY_B.
3. KEY_B: input0=round_key[7:0], input1=round_key[31:24]. g is EXACTLY 32 bits: {g_hi,S0,S1} XOR {rcon,24'b0}. n0=round_key[127:96]^g; n1=round_key[95:64]^n0; n2=round_key[63:32]^n1; n3=round_key[31:0]^n2. Commit round_key={n0,n1,n2,n3}, rcon=xtime(rcon), sub_count=0; enter SUB.
4. SUB, EIGHT cycles: inputs=state_reg[127:120],state_reg[119:112]. Each edge state_reg<={state_reg[111:0],S0,S1}. After the eighth edge state_reg is exactly SubBytes(old_state) in the original order. Enter ROUND.
5. ROUND: ShiftRows reads state_reg, NOT the SUB D-input sub_rot (that would perform a ninth substitution). Fixed wiring and full MixColumns as below. round_value=((round==10)?shiftrows:mixcolumns)^round_key. Both branches XOR the round key, including K10! Commit state_reg<=round_value. If round==10 enter DONE, otherwise round++ and KEY_A. Total 110 cycles, within 200.
There must be exactly two function calls outside phase case, fed by the input mux; no separately instantiated key/state S-box logic. In inactive phases input mux may hold or select zero. Ciphertext aliases state_reg; do not create a separate output register. done=(phase==DONE), ready=(phase==IDLE). On DONE return edge, clear done/return idle or accept start and begin next job. Hold state_reg throughout DONE until its trailing edge.

Byte ordering: byte i = state[127-8*i -: 8], state[r][c]=byte[4*c+r]. ShiftRows output byte i, r=i%4,c=i/4, takes substituted input byte 4*((c+r)%4)+r. Use constant indices/generate wiring. MixColumns operates on each column bytes a,b,c,d, with t=a^b^c^d; outputs a^t^xtime(a^b), b^t^xtime(b^c), c^t^xtime(c^d), d^t^xtime(d^a). xtime(x)={x[6:0],1'b0} XOR (x[7]?8'h1b:0). Final round bypasses MixColumns. No byte reversal anywhere. Use the exact standard forward AES S-box as a complete 256-entry case function, not an unverified GF inversion formula. All helper functions inside the one module; assign to function name, no return statement.

Check these exact vectors mentally for key/byte/round sequencing: K=000102030405060708090a0b0c0d0e0f P=00112233445566778899aabbccddeeff C=69c4e0d86a7b0430d8cdb78070b4c55a; all-zero K/P C=66e94bd4ef8a2c3b884cfa59ca342b2e; K=2b7e151628aed2a6abf7158809cf4f3c P=3243f6a8885a308d313198a2e0370734 C=3925841d02dc09fbdc118597196a0b32.

Use complete combinational assignments, no inferred latches, no shared procedural loop variables between blocks. Pure Verilog-style untyped localparam constants and function automatic [7:0] sbox(input [7:0] a). Store no unnecessary input copies or SubBytes buffer. Hold registers in inactive phases. Ciphertext may be a combinational alias of the final state register during DONE to avoid an extra 128-bit register, as long as it stays stable throughout done. Synthesize with read_verilog -sv and Icarus -g2012.

Mandatory round connection (previous sample omitted ShiftRows before MixColumns):
wire [127:0] sr_val = shiftrows(state_reg);
wire [127:0] mc_val = mixcolumns(sr_val);
wire [127:0] round_value = ((round==10) ? sr_val : mc_val) ^ round_key;
Never call mixcolumns(state_reg): that incorrectly skips ShiftRows in rounds 1..9.
Only the standard forward S-box table may be used; check all 256 entries. Keep byte0 at bits 127:120 in every intermediate value.
