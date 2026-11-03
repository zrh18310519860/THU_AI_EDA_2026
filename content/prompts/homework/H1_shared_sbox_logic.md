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

Byte ordering: byte i = state[127-8*i -: 8], state[r][c]=byte[4*c+r]. ShiftRows output byte i, r=i%4,c=i/4, takes substituted input byte 4*((c+r)%4)+r. Use constant indices/generate wiring. MixColumns operates on each column bytes a,b,c,d, with t=a^b^c^d; outputs a^t^xtime(a^b), b^t^xtime(b^c), c^t^xtime(c^d), d^t^xtime(d^a). xtime(x)={x[6:0],1'b0} XOR (x[7]?8'h1b:0). Final round bypasses MixColumns. No byte reversal anywhere. Use exactly the scalar Boolean aes_sbox_logic function supplied below; it has been exhaustively checked on all 256 inputs. Do not use a 256-entry case or ROM. Inline the supplied function inside aes128 without modifying the equations. Retain its license comment. This is embedded combinational logic, not an external module or runtime file. All helper functions inside the one module; assign to function name, no return statement.

Check these exact vectors mentally for key/byte/round sequencing: K=000102030405060708090a0b0c0d0e0f P=00112233445566778899aabbccddeeff C=69c4e0d86a7b0430d8cdb78070b4c55a; all-zero K/P C=66e94bd4ef8a2c3b884cfa59ca342b2e; K=2b7e151628aed2a6abf7158809cf4f3c P=3243f6a8885a308d313198a2e0370734 C=3925841d02dc09fbdc118597196a0b32.

Use complete combinational assignments, no inferred latches, no shared procedural loop variables between blocks. Pure Verilog-style untyped localparam constants and function automatic [7:0] sbox(input [7:0] a). Store no unnecessary input copies or SubBytes buffer. Hold registers in inactive phases. Ciphertext may be a combinational alias of the final state register during DONE to avoid an extra 128-bit register, as long as it stays stable throughout done. Synthesize with read_verilog -sv and Icarus -g2012.

Mandatory register reuse: assign ciphertext = state_reg; the state register already contains the final result. Do not create a separate ciphertext register. Four shared calls to aes_sbox_logic only, fed by phase-controlled input muxes.

/*
 * Copyright (c) 2016 Thomas Pornin <pornin@bolet.org>
 *
 * Permission is hereby granted, free of charge, to any person obtaining 
 * a copy of this software and associated documentation files (the
 * "Software"), to deal in the Software without restriction, including
 * without limitation the rights to use, copy, modify, merge, publish,
 * distribute, sublicense, and/or sell copies of the Software, and to
 * permit persons to whom the Software is furnished to do so, subject to
 * the following conditions:
 *
 * The above copyright notice and this permission notice shall be 
 * included in all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, 
 * EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 * MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND 
 * NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
 * BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
 * ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
 * CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
function automatic [7:0] aes_sbox_logic(input [7:0] a);

	

	reg x0, x1, x2, x3, x4, x5, x6, x7;
	reg y1, y2, y3, y4, y5, y6, y7, y8, y9;
	reg y10, y11, y12, y13, y14, y15, y16, y17, y18, y19;
	reg y20, y21;
	reg z0, z1, z2, z3, z4, z5, z6, z7, z8, z9;
	reg z10, z11, z12, z13, z14, z15, z16, z17;
	reg t0, t1, t2, t3, t4, t5, t6, t7, t8, t9;
	reg t10, t11, t12, t13, t14, t15, t16, t17, t18, t19;
	reg t20, t21, t22, t23, t24, t25, t26, t27, t28, t29;
	reg t30, t31, t32, t33, t34, t35, t36, t37, t38, t39;
	reg t40, t41, t42, t43, t44, t45, t46, t47, t48, t49;
	reg t50, t51, t52, t53, t54, t55, t56, t57, t58, t59;
	reg t60, t61, t62, t63, t64, t65, t66, t67;
	reg s0, s1, s2, s3, s4, s5, s6, s7;

	x0 = a[7];
	x1 = a[6];
	x2 = a[5];
	x3 = a[4];
	x4 = a[3];
	x5 = a[2];
	x6 = a[1];
	x7 = a[0];

	
	y14 = x3 ^ x5;
	y13 = x0 ^ x6;
	y9 = x0 ^ x3;
	y8 = x0 ^ x5;
	t0 = x1 ^ x2;
	y1 = t0 ^ x7;
	y4 = y1 ^ x3;
	y12 = y13 ^ y14;
	y2 = y1 ^ x0;
	y5 = y1 ^ x6;
	y3 = y5 ^ y8;
	t1 = x4 ^ y12;
	y15 = t1 ^ x5;
	y20 = t1 ^ x1;
	y6 = y15 ^ x7;
	y10 = y15 ^ t0;
	y11 = y20 ^ y9;
	y7 = x7 ^ y11;
	y17 = y10 ^ y11;
	y19 = y10 ^ y8;
	y16 = t0 ^ y11;
	y21 = y13 ^ y16;
	y18 = x0 ^ y16;

	
	t2 = y12 & y15;
	t3 = y3 & y6;
	t4 = t3 ^ t2;
	t5 = y4 & x7;
	t6 = t5 ^ t2;
	t7 = y13 & y16;
	t8 = y5 & y1;
	t9 = t8 ^ t7;
	t10 = y2 & y7;
	t11 = t10 ^ t7;
	t12 = y9 & y11;
	t13 = y14 & y17;
	t14 = t13 ^ t12;
	t15 = y8 & y10;
	t16 = t15 ^ t12;
	t17 = t4 ^ t14;
	t18 = t6 ^ t16;
	t19 = t9 ^ t14;
	t20 = t11 ^ t16;
	t21 = t17 ^ y20;
	t22 = t18 ^ y19;
	t23 = t19 ^ y21;
	t24 = t20 ^ y18;

	t25 = t21 ^ t22;
	t26 = t21 & t23;
	t27 = t24 ^ t26;
	t28 = t25 & t27;
	t29 = t28 ^ t22;
	t30 = t23 ^ t24;
	t31 = t22 ^ t26;
	t32 = t31 & t30;
	t33 = t32 ^ t24;
	t34 = t23 ^ t33;
	t35 = t27 ^ t33;
	t36 = t24 & t35;
	t37 = t36 ^ t34;
	t38 = t27 ^ t36;
	t39 = t29 & t38;
	t40 = t25 ^ t39;

	t41 = t40 ^ t37;
	t42 = t29 ^ t33;
	t43 = t29 ^ t40;
	t44 = t33 ^ t37;
	t45 = t42 ^ t41;
	z0 = t44 & y15;
	z1 = t37 & y6;
	z2 = t33 & x7;
	z3 = t43 & y16;
	z4 = t40 & y1;
	z5 = t29 & y7;
	z6 = t42 & y11;
	z7 = t45 & y17;
	z8 = t41 & y10;
	z9 = t44 & y12;
	z10 = t37 & y3;
	z11 = t33 & y4;
	z12 = t43 & y13;
	z13 = t40 & y5;
	z14 = t29 & y2;
	z15 = t42 & y9;
	z16 = t45 & y14;
	z17 = t41 & y8;

	
	t46 = z15 ^ z16;
	t47 = z10 ^ z11;
	t48 = z5 ^ z13;
	t49 = z9 ^ z10;
	t50 = z2 ^ z12;
	t51 = z2 ^ z5;
	t52 = z7 ^ z8;
	t53 = z0 ^ z3;
	t54 = z6 ^ z7;
	t55 = z16 ^ z17;
	t56 = z12 ^ t48;
	t57 = t50 ^ t53;
	t58 = z4 ^ t46;
	t59 = z3 ^ t54;
	t60 = t46 ^ t57;
	t61 = z14 ^ t57;
	t62 = t52 ^ t58;
	t63 = t49 ^ t58;
	t64 = z4 ^ t59;
	t65 = t61 ^ t62;
	t66 = z1 ^ t63;
	s0 = t59 ^ t63;
	s6 = t56 ^ ~t62;
	s7 = t48 ^ ~t60;
	t67 = t64 ^ t65;
	s3 = t53 ^ t66;
	s4 = t51 ^ t66;
	s5 = t47 ^ t65;
	s1 = t64 ^ ~s3;
	s2 = t55 ^ ~t67;

	aes_sbox_logic[7] = s0;
	aes_sbox_logic[6] = s1;
	aes_sbox_logic[5] = s2;
	aes_sbox_logic[4] = s3;
	aes_sbox_logic[3] = s4;
	aes_sbox_logic[2] = s5;
	aes_sbox_logic[1] = s6;
	aes_sbox_logic[0] = s7;

endfunction

Explicit correctness guardrails from failed samples:
* g is EXACTLY 32 bits, not 128: wire [31:0] g = {sb0,sb1,sb2,sb3} ^ {rcon,24'b0}; n0=round_key[127:96]^g. Never take g[127:96] (that silently gives zero).
* After FOUR SUB edges state_reg ALREADY holds SubBytes(old_state) in correct order. In ROUND phase ShiftRows MUST read state_reg, NOT sub_rot/sub_shift. sub_rot is only the D input used in SUB; reading it in ROUND adds an erroneous fifth substitution/rotation.
* round_data = ((round==10) ? shiftrows_of_state_reg : mixcolumns_of_shiftrows) ^ round_key. XOR K10 in the final round too. Parenthesize the mux BEFORE XOR.
* ready=0 throughout DONE; ciphertext aliases state_reg; clear done on the next edge. No extra final-round state change until next accepted job.
