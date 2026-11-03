"""Create the final two-S-box architecture prompt without modifying teacher material."""
from ai_eda.config import PROJECT_ROOT


def main():
    prompt=(PROJECT_ROOT/'content/prompts/homework/H1_shared_sbox.md').read_text()
    begin=prompt.index('Architecture is fixed')
    end=prompt.index('Byte ordering:')
    prompt=prompt[:begin]+'''Architecture is fixed to TWO shared forward S-boxes total, time-multiplexed between state and key expansion. Current state and current key are 128-bit each, no eleven-key table. Use a round counter 1..10, phase IDLE/KEY_A/KEY_B/SUB/ROUND/DONE, SUB counter 0..7, and 16-bit g_hi:
1. Start edge: state_reg=plaintext XOR key, round_key=key, rcon=1, round=1; enter KEY_A.
2. KEY_A: S-box input0=round_key[23:16], input1=round_key[15:8]. Store g_hi={S0,S1}; enter KEY_B.
3. KEY_B: input0=round_key[7:0], input1=round_key[31:24]. g is EXACTLY 32 bits: {g_hi,S0,S1} XOR {rcon,24'b0}. n0=round_key[127:96]^g; n1=round_key[95:64]^n0; n2=round_key[63:32]^n1; n3=round_key[31:0]^n2. Commit round_key={n0,n1,n2,n3}, rcon=xtime(rcon), sub_count=0; enter SUB.
4. SUB, EIGHT cycles: inputs=state_reg[127:120],state_reg[119:112]. Each edge state_reg<={state_reg[111:0],S0,S1}. After the eighth edge state_reg is exactly SubBytes(old_state) in the original order. Enter ROUND.
5. ROUND: ShiftRows reads state_reg, NOT the SUB D-input sub_rot (that would perform a ninth substitution). Fixed wiring and full MixColumns as below. round_value=((round==10)?shiftrows:mixcolumns)^round_key. Both branches XOR the round key, including K10! Commit state_reg<=round_value. If round==10 enter DONE, otherwise round++ and KEY_A. Total 110 cycles, within 200.
There must be exactly two function calls outside phase case, fed by the input mux; no separately instantiated key/state S-box logic. In inactive phases input mux may hold or select zero. Ciphertext aliases state_reg; do not create a separate output register. done=(phase==DONE), ready=(phase==IDLE). On DONE return edge, clear done/return idle or accept start and begin next job. Hold state_reg throughout DONE until its trailing edge.

'''+prompt[end:]
    prompt=prompt.replace('four highest state bytes','two highest state bytes')
    prompt += '''
Mandatory round connection (previous sample omitted ShiftRows before MixColumns):
wire [127:0] sr_val = shiftrows(state_reg);
wire [127:0] mc_val = mixcolumns(sr_val);
wire [127:0] round_value = ((round==10) ? sr_val : mc_val) ^ round_key;
Never call mixcolumns(state_reg): that incorrectly skips ShiftRows in rounds 1..9.
Only the standard forward S-box table may be used; check all 256 entries. Keep byte0 at bits 127:120 in every intermediate value.
'''
    (PROJECT_ROOT/'content/prompts/homework/H1_two_sbox.md').write_text(prompt)


if __name__=='__main__':main()
