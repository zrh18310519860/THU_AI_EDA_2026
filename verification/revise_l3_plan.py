"""Explicit contract revision after the first real failed integration experiment."""

import json
from pathlib import Path

ROOT = Path("/home/eda/projects/THU_AI_EDA_2026")
SOURCE = ROOT/"runs/agent/agent-20261027T100154018081/artifacts/plan.json"
plan = json.loads(SOURCE.read_text())
for module in plan["modules"]:
    ports = module["interfaces"]
    if module["name"] in {"icache", "dcache"}:
        ports = ports.replace("icache_cpu_", "cpu_").replace("dcache_cpu_", "cpu_")
    if module["name"] == "rename_unit":
        for port in ("prf_rd_en0", "prf_rd_en1"):
            ports = ports.replace("output logic " + port, "input logic " + port)
        for port in ("prf_rd_addr0", "prf_rd_addr1"):
            ports = ports.replace("output logic [5:0] " + port, "input logic [5:0] " + port)
        for port in ("prf_rdata0", "prf_rdata1"):
            ports = ports.replace("input logic [31:0] " + port, "output logic [31:0] " + port)
        ports += "; input logic [3:0] rob_head_id; input logic [3:0] commit_rob_id"
    module["interfaces"] = ports
    module["description"] += ". Synchronous reset ONLY, no typed parameters."
plan["notes"] += '''
CONTRACT REVISION v2 (overrides contradictory earlier notes):
1. rename_unit OWNS PRF storage. IQ prf_rd_addr/en outputs wire to rename input ports;
rename prf_rdata outputs wire to IQ inputs. No shadow PRF or dangling PRF data wires.
2. Free list is a 40-bit FREE bitmap (0 means allocated), with rotating 6-bit head cursor.
Reset free bitmap: PR32..39 free, PR0..31 allocated. ALL nonzero old_prd may be freed
at commit, including PR1..31 (not only PR>31). Same-cycle commit and allocation must
both take effect, using combined next-state masks; PR0 always allocated/ready/zero.
Track each in-flight ROB id's newly allocated PR and valid bit. Squash only reclaims
younger ids using ((id-rob_head_id)&7) > ((branch_id-rob_head_id)&7). Restore RAT and
cursor from per-control checkpoint, preserve own JAL allocation. Do NOT restore old
free bitmap/tail/count which would lose intervening commits; merge reclaimed PR bits
into CURRENT availability using allocated mask. Clear only reclaimed PR ready bits.
Invalidate younger checkpoints and committed checkpoint slots. Ecall_pending clears
on branch squash, dispatch is blocked while pending; own ECALL is complete in ROB.
3. IQ age is (entry_rob_id-rob_head_id)&7, NEVER a wrapping timestamp counter.
Gate memory issue and control-transfer issue to ROB head; ALUs remain out-of-order.
On squash no new dispatch or unrelated issue; resolving branch completion survives.
4. Single completion bus: LSU response has priority; integer issue_ready=0 on an LSU
completion cycle. No dropped ALU completion. Shared issue/prf data is combinational.
5. ROB physical slot ids 0..7, wrap modulo8; depth counter 0..8. Concurrent allocate
and retire net count unchanged. Commit handshake is COMBINATIONAL head-valid&done;
rename commits on that edge. ROB retire outputs are registered pulses of that edge.
Do NOT accidentally commit twice via registered commit_valid. Complete sets done
for live slot only. ECALL complete on allocate. Top sets halted one edge AFTER ECALL
retire pulse, with final committed arch_x10. No speculative ECALL irreversible latch.
6. Top combinational valid outputs gated rst_n, no requests before first reset edge.
Front-end stop_fetch=(rename ecall_pending||halted); epoch increments on actual ECALL
dispatch and branch redirect, not delayed ECALL commit. Flush I cache on either, but
NOT D cache when a younger ECALL is dispatched or a branch resolves: all D operations
are ROB-head safe and older loads/stores must finish. Preserve bus address when idle
and halted, drain current held I word before halt; no new wrong-path refill words.
7. Cache response is registered one-cycle valid. External mem_resp_valid in top is
mem_req_valid&&mem_req_ready and data from external rdata in SAME handshake cycle.
I-cache flush: if held mem_req_valid&&!ready, retain word request until ready then
discard, cancel later words. If no active external word, cancel immediately. An
in-progress replaced line valid bit is cleared at miss start; epoch mismatch never
installs tag/data/valid. 24-bit tags address[31:8], not 16-bit. Data/tag need no reset.
8. Separate module loop variables for each procedural block (no shared integer i).
Memory LSQ2 physical entries hold full metadata; head-only memory launch is allowed.
Do not hardcode benchmark values. Full RV32I semantics from teacher spec. No global
freeze of younger independent ALU while a load is waiting. dbg_ooo_issue pulse counts
ALL true IQ issue_accept (including memory); bypass reflects actual older D wait.
9. alu_op[4:0] frozen: ADD0 SUB1 SLL2 SLT3 SLTU4 XOR5 SRL6 SRA7 OR8 AND9;
execute uses ctrl[11] for ALU and selects immediate if !ctrl[1], else ex_src2;
LUI/AUIPC and JAL/JALR handled explicitly. No use of ctrl[0] to infer ALU operand A
for LUI/AUIPC. For branches funct3 compares raw source operands.
'''
target=Path("/mnt/f/AI_EDA/L3-evidence-20261027/plan_v2.json")
target.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
print(target)
