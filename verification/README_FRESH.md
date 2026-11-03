# L3 From-Scratch Exploration (2026-10-28)

Normal Run Agent starts an empty per-run project. New baseline-reuse requests are
rejected, including stale browser/API requests. Historical runs remain intact.
Resume is an explicit separate action and is labelled resumed, not fresh.

1. Parser makes a real model call to interpret the self-contained teacher spec.
2. Planner makes a real model call to define modules, ports, dependencies and
   cycle-accurate protocols. No stored plan or L2 seed is imported.
3. Coder generates leaf modules before consumers and top last. Each file uses a
   real model call and has local Icarus/Yosys parsing gates. Related generated
   peer excerpts are bounded to 24,000 characters; old-run RTL is unavailable.
   If two local syntax corrections still fail, generation continues and the real
   compiler failure is handed to the dedicated Tester/Diagnoser/Modifier loop.
4. Tester runs smoke checks, then the unchanged teacher test and independent
   stress test. PPA is evaluated only after functional checks pass.
5. Diagnoser receives bounded failure evidence; Modifier emits hash-checked,
   exact-anchor patches. Every candidate is retested. Failed attempts are kept.
   Selected source is bounded to 96k characters total (64k per file), retaining
   complete modules when possible. Omitted text is explicitly labelled and is
   never evidence that a signal has no existing driver. Large logs remain compact.
6. PPA candidates are compared only when functional tests and measurement pass.
   Failure never triggers fallback to built-in RTL or fabricated results.

Audit artifacts: exploration_provenance.json records the empty start and seed
policy; generation_provenance.json records each file, model call indices, hashes
and revisions. Per-step call-NNN directories preserve prompts, conversations,
raw answers, reasoning and API usage/timing for every validation retry.
tested_manifest_N.json and patch_manifest_N.json preserve immutable candidates.
L3 Planner/Coder use non-thinking structured output after a real 32k-token
planning request exhausted its budget without a final JSON answer. Parser keeps
the run-level setting; Diagnoser/Modifier also use non-thinking calls. Actual
per-role parameters, rather than only the requested global setting, are recorded.

Run in WSL at /home/eda/projects/THU_AI_EDA_2026:

    .venv/bin/python -m verification.run_l3 --optimize 2
    .venv/bin/python -m unittest discover -s verification -p 'test_*.py'

Actual exploration evidence is written under F:/AI_EDA/fresh-exploration-20261028.
The 2026-10-27 baseline-reuse successes remain historical reuse evidence, not
independent generation results. Passing finite tests does not prove universal
correctness or guarantee every future stochastic generation will pass.

## Actual Verification

| Run | Mode | Real API calls | Total tokens | Wall seconds | Result |
| --- | --- | ---: | ---: | ---: | --- |
| agent-20261027T163358777811 | fresh | 2 | 63849 | 268.617 | Planner reasoning exhausted 32k; no final plan |
| agent-20261027T164051846072 | fresh | 6 | 97036 | 68.945 | 2 of 10 files generated; local function syntax gate failed |
| agent-20261027T164511671068 | fresh | 16 | 355651 | 171.659 | All 8 files model-generated; 4 Tester attempts, functional startup failure |
| agent-20261027T165333144311 | explicit resume of the preceding run, snapshot 2 | 6 | 254889 | 43.997 | 3 more actual Tester executions; functional startup failure remains |

All eight initial RTL files in the complete fresh run were audited against the
actual raw model responses and hashes. Its three diagnosis/patch cycles are real,
as are the three later resume cycles. None imported built-in RTL. These runs have
NOT passed the teacher acceptance suite. Functional gates skip PPA; no new area
or power result is claimed. The historical baseline-reuse successes are not a
substitute for a successful fresh generation. The 31 software regression tests
and desktop/mobile UI checks verify the workflow, not CPU functional correctness.
