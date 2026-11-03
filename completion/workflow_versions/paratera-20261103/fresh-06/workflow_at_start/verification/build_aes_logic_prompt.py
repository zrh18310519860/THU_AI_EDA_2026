"""Mechanically port a licensed bitwise S-box circuit, then exhaustively validate it."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

from ai_eda.config import PROJECT_ROOT


def main():
    evidence=Path('/mnt/f/AI_EDA/stability-evidence-20261027')
    source=(evidence/'bearssl_aes_ct.c').read_text()
    license=source[:source.index('*/')+2]
    body=source.split('br_aes_ct_bitslice_Sbox(uint32_t *q)',1)[1].split('/* see inner.h */',1)[0]
    body=body[body.index('{')+1:body.rindex('}')]
    body=re.sub(r'/\*.*?\*/','',body,flags=re.S)
    body=body.replace('uint32_t','reg')
    body=re.sub(r'(?m)^(\s*)q\[(\d)\](\s*=)',r'\1aes_sbox_logic[\2]\3',body)
    body=re.sub(r'\bq\[(\d)\]',r'a[\1]',body)
    function=license+'\nfunction automatic [7:0] aes_sbox_logic(input [7:0] a);\n'+body+'\nendfunction\n'
    spec=(PROJECT_ROOT/'content/tasks/homework_aes128/spec.md').read_text()
    values=[]
    for line in spec.splitlines():
        if re.match(r'^0x[0-9a-f]{2}\s',line):
            row=line.split()[1:];assert len(row)==16;values.extend(row)
    assert len(values)==256
    tb=evidence/'sbox_exhaustive.sv'
    tb.write_text('module tb_sbox;\n'+function+"\nlocalparam [2047:0] TABLE=2048'h"+''.join(values)+";\ninteger i; initial begin for(i=0;i<256;i=i+1) if(aes_sbox_logic(i[7:0]) !== TABLE[2047-8*i -:8]) $fatal(1,\"SBOX mismatch %0d\",i); $display(\"SBOX_EXHAUSTIVE: PASS 256/256\"); $finish; end endmodule\n")
    executable=evidence/'sbox_exhaustive.vvp'
    subprocess.run(['iverilog','-g2012','-s','tb_sbox','-o',str(executable),str(tb)],check=True)
    result=subprocess.run(['vvp',str(executable)],capture_output=True,text=True,check=True)
    (evidence/'sbox_exhaustive.log').write_text(result.stdout)
    prompt=(PROJECT_ROOT/'content/prompts/homework/H1_shared_sbox.md').read_text()
    prompt=prompt.replace('Use the exact standard forward AES S-box as a complete 256-entry case function, not an unverified GF inversion formula.',
        'Use exactly the scalar Boolean aes_sbox_logic function supplied below; it has been exhaustively checked on all 256 inputs. Do not use a 256-entry case or ROM. Inline the supplied function inside aes128 without modifying the equations. Retain its license comment. This is embedded combinational logic, not an external module or runtime file.')
    prompt += '\nMandatory register reuse: assign ciphertext = state_reg; the state register already contains the final result. Do not create a separate ciphertext register. Four shared calls to aes_sbox_logic only, fed by phase-controlled input muxes.\n\n'+function
    prompt += '''
Explicit correctness guardrails from failed samples:
* g is EXACTLY 32 bits, not 128: wire [31:0] g = {sb0,sb1,sb2,sb3} ^ {rcon,24'b0}; n0=round_key[127:96]^g. Never take g[127:96] (that silently gives zero).
* After FOUR SUB edges state_reg ALREADY holds SubBytes(old_state) in correct order. In ROUND phase ShiftRows MUST read state_reg, NOT sub_rot/sub_shift. sub_rot is only the D input used in SUB; reading it in ROUND adds an erroneous fifth substitution/rotation.
* round_data = ((round==10) ? shiftrows_of_state_reg : mixcolumns_of_shiftrows) ^ round_key. XOR K10 in the final round too. Parenthesize the mux BEFORE XOR.
* ready=0 throughout DONE; ciphertext aliases state_reg; clear done on the next edge. No extra final-round state change until next accepted job.
'''
    (PROJECT_ROOT/'content/prompts/homework/H1_shared_sbox_logic.md').write_text(prompt)
    (evidence/'sbox_reference.json').write_text(json.dumps({'source_url':'https://www.bearssl.org/gitweb/?p=BearSSL;a=blob_plain;f=src/symcipher/aes_ct.c;hb=HEAD',
        'sha256':hashlib.sha256(source.encode()).hexdigest(),'license':'MIT, Thomas Pornin 2016',
        'validation':result.stdout.strip(),'adaptation':'Mechanical uint32_t bit-slice to scalar reg; q[k] maps to a[k] and result[k].'},indent=2))
    print(result.stdout,flush=True)


if __name__=='__main__':main()
