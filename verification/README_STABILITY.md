# 2026-10-27 stability and PPA review

Historical record only. As of 2026-10-28, built-in L3 baseline reuse is disabled
for all new runs. Normal Run Agent starts fresh; explicit Resume is labelled as
resumed. Reproduce fresh exploration with `.venv/bin/python -m verification.run_l3`.
See README_FRESH.md for the current workflow. The old baseline figures below
must not be interpreted as independent from-scratch generations.

## Reproduction

Runtime: WSL AI-EDA-Ubuntu-22.04, /home/eda/projects/THU_AI_EDA_2026.
Use .venv/bin/python. Tools, installed dependencies and evidence live on F:.

1. Start the workbench: `.venv/bin/python -m ai_eda serve --host 127.0.0.1 --port 8000`.
2. AES: Prompt / Homework, H1_custom.md. The final prompt uses two shared lookup S-boxes and 110 cycles.
3. L3: rv32i5_ooo, verified_incremental, max_tokens=24000, max_calls=16,
   max_repair_attempts=4, optimization_rounds=2, thinking on/low.
4. Run `python -m unittest discover -s verification -p 'test_*.py'`.
5. Reproduce batches: `python -m verification.run_stability_experiments aes --count 3 --two`
   then `python -m verification.run_stability_experiments metrics --two`.
   Run `verification.aes_oracle.validate(run_id)` for the independent 100-vector check.
6. L3 repetitions: `python -m verification.run_stability_experiments l3 --count 3`.
7. Export: `python -m verification.export_stability_evidence`.

## Scope of stability

Three L3 runs independently invoke optimization models but import the SAME frozen,
human-assisted baseline from agent-20261027T111341960911, tested snapshot 4.
Planner/Coder reuse events are explicit and are not counted as model calls.
The reference plan and 11 RTL sources are SHA-256 checked on every import.
All nine tested candidates passed. The best verified snapshot is retained when a
proposal fails or regresses. This is NOT three independent from-scratch generations.
Fresh mode and recovery runs still have known startup/retirement failures and are
preserved as experimental evidence. API outages and unseen functional defects
remain possible; passing supplied tests is not a proof for all programs.

## Measurement protocol

Yosys 0.70+post (official v0.70 source, commit 0c11ea098be065e585a90a06e7b7da49936cf231),
external ABC, OpenSTA 3.1.0, gscl45nm.lib, 10 ns clock. Exact versions, library and RTL
hashes are recorded in each new PPA result. All teacher Spec/TB files are compared
against Git HEAD in teacher_integrity.json; none are weakened.

Course power estimates use the mapped netlist with original RTL power-TB VCD.
Only matching signals are annotated; other activity is propagated/defaulted.
It is a pre-layout estimate, not a physical signoff result. Values in JSON are W;
multiply by 1000 for mW or 1e6 for uW. Area remains Liberty library area units,
not cell count or a guessed normalized number. Candidates minimize area * power.

gate_activity_crosscheck is a separate zero-delay mapped-VCD diagnostic and never
silently replaces the course result. It also reruns the original functional TB.
Compact Boolean S-boxes show sensitivity to activity annotation; the report keeps
both estimates separate and selects by the stated course protocol.

The TA screenshot says approximately 1000 and 130 but supplies no units, design,
clock or tool version. Those values cannot yet be compared numerically. Ask for
the area unit (cell count versus library/physical area), power unit, reference AES
architecture, synthesis flow and activity assumptions before drawing a conclusion.

Final evidence: F:/AI_EDA/stability-evidence-20261027. Failed attempts are retained;
usage comes from actual API responses, and stage timing excludes report writing.
No API key is included in the report or submission archive.
