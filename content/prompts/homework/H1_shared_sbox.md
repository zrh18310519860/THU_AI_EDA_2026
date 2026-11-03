Implement a single synthesizable SystemVerilog module aes128 for FIPS-197 AES-128 encryption, prioritizing low mapped area and switching. Output only complete RTL, no Markdown, explanation, or testbench. No other modules, includes, external files, initial blocks, delays or test-vector special cases.

Exact interface:
module aes128(input logic clk, input logic rst_n, input logic start,
 input logic [127:0] key, input logic [127:0] plaintext,
 output logic ready, output logic done, output logic [127:0] ciphertext);

Use synchronous active-low reset in always @(posedge clk). Reset cancels any job, done=0, ready=1. Accept only start while idle, latch both inputs then. Ignore start while busy. done is exactly one cycle, ready=0 during done. On the following edge return to idle (ready=1, done=0); allow a new start on this return edge as well. Ciphertext is stable throughout done. No async reset. Maximum latency 200 cycles, clock period 10 ns.

Architecture is fixed to FOUR shared forward S-boxes total, reused by both state and key expansion. Do not create 16 state S-boxes or separate key S-boxes. Do not precompute eleven round keys. Keep a 128-bit current state and 128-bit current key. Use a small round counter 1..10 and explicit KEY, SUB, ROUND, DONE phases:
1. Accepted start: state = plaintext XOR key; round_key = key; round=1.
2. KEY, one cycle: select RotWord(round_key[31:0]) for the SAME four S-box inputs used in SUB. Let g={S(k[23:16]),S(k[15:8]),S(k[7:0]),S(k[31:24])} XOR {rcon,24'b0}. n0=k[127:96]^g; n1=k[95:64]^n0; n2=k[63:32]^n1; n3=k[31:0]^n2. Commit round_key={n0,n1,n2,n3}. Update rcon=xtime(rcon), initial rcon=1. This key is K_round.
3. SUB, four cycles: the four S-box inputs are the four highest state bytes. Each cycle state <= {state[95:0],sb0,sb1,sb2,sb3}. After four such rotations, all sixteen bytes have been substituted exactly once in their original order. The four S-box function calls must be outside the phase case, fed by the input mux, so synthesis shares four instances, not eight or twenty.
4. ROUND, one cycle: form ShiftRows by FIXED wiring (no dynamic index into a 128-bit word); optionally MixColumns for round<10, XOR current round_key. Commit state, or for round==10 latch ciphertext and enter DONE. For earlier rounds increment round and go to KEY. Thus about 60 cycles, not 200 S-box serial cycles.

Byte ordering: byte i = state[127-8*i -: 8], state[r][c]=byte[4*c+r]. ShiftRows output byte i, r=i%4,c=i/4, takes substituted input byte 4*((c+r)%4)+r. Use constant indices/generate wiring. MixColumns operates on each column bytes a,b,c,d, with t=a^b^c^d; outputs a^t^xtime(a^b), b^t^xtime(b^c), c^t^xtime(c^d), d^t^xtime(d^a). xtime(x)={x[6:0],1'b0} XOR (x[7]?8'h1b:0). Final round bypasses MixColumns. No byte reversal anywhere. Use the exact standard forward AES S-box as a complete 256-entry case function, not an unverified GF inversion formula. All helper functions inside the one module; assign to function name, no return statement.

Check these exact vectors mentally for key/byte/round sequencing: K=000102030405060708090a0b0c0d0e0f P=00112233445566778899aabbccddeeff C=69c4e0d86a7b0430d8cdb78070b4c55a; all-zero K/P C=66e94bd4ef8a2c3b884cfa59ca342b2e; K=2b7e151628aed2a6abf7158809cf4f3c P=3243f6a8885a308d313198a2e0370734 C=3925841d02dc09fbdc118597196a0b32.

Use complete combinational assignments, no inferred latches, no shared procedural loop variables between blocks. Pure Verilog-style untyped localparam constants and function automatic [7:0] sbox(input [7:0] a). Store no unnecessary input copies or SubBytes buffer. Hold registers in inactive phases. Ciphertext may be a combinational alias of the final state register during DONE to avoid an extra 128-bit register, as long as it stays stable throughout done. Synthesize with read_verilog -sv and Icarus -g2012.
