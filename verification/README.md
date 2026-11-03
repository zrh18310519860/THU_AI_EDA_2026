# Run records and experiment reproduction

The workbench now records actual request parameters, API input/output/total
tokens, generation duration, verification duration and run summaries for
Homework and Agent runs. Missing usage is reported as unavailable; estimated
tokens are never reported as API usage. Agent overrides are saved per call.
Prompt time to TB includes generation and any wait before the first TB result;
PPA is timed separately. Agent duration covers the whole workflow.

## Environment

- Editable source base: e25f015 plus local changes, not committed.
- Deployed source: /home/eda/projects/THU_AI_EDA_2026 in AI-EDA-Ubuntu-22.04.
- Python 3.10.12, Icarus Verilog 11.0, Yosys 0.9 (1979e0b), OpenSTA 3.1.0.
- Standard cell library: gscl45nm. The teacher power TB uses a 10 ns clock.
- The teacher's specification describes a newer Yosys; these results use the
  actual installed version above and should only be compared within that setup.

From the extracted project directory in Ubuntu/WSL, using an environment with
the project dependencies and EDA tools installed:

```sh
python -m ai_eda serve --host 127.0.0.1 --port 8000
python -m unittest verification.test_run_records -v
```

The twelve focused regression tests pass without network requests or EDA tools.
The configured API key is deliberately excluded from this archive. Configure
your own key locally before generating any new RTL.

## Saved experiments

The archive includes the exact prompts, model outputs, RTL, API usage, teacher
TB reports, PPA reports and activity data under runs/. Open Prompt -> Homework
to inspect saved AES runs. Run TB/Evaluate metrics reruns local verification
without another model call. Generate RTL starts a new paid API request and can
produce a different design.

| AES run ID | TB | API total tokens | Generation to first TB | Area | Total power |
| --- | --- | ---: | ---: | ---: | ---: |
| hw-20261027T083728442998 | compile FAIL | 3975 | 11.4005 s | N/A | N/A |
| hw-20261027T083740003153 | simulation FAIL | 7136 | 19.9323 s | N/A | N/A |
| hw-20261027T083757481897 | PASS | 6710 | 18.2799 s | 45857.1802 | 0.0264 W |
| hw-20261027T083853818340 | PASS | 7777 | 17.5759 s | 43592.3384 | 0.0321 W |

These are independent resamples of four saved prompt versions, not replacements
of the original runs. The last run is the area-priority final; the third uses
less power. AES API total usage is 25598 tokens.

Agent baseline agent-20261027T083946373622 uses stream_filter: teacher TB PASS,
5 API calls, 12667 input + 3330 output = 15997 total tokens, 22.918 s overall.
PPA failed because Yosys 0.9 rejected a generated typed parameter declaration.
This baseline verifies recording, not the RISC-V Agent-refactoring assignment.
Combined new usage is 41595 total tokens.

## Evidence and clock correction

The evidence/ directory contains full run exports, a CSV and tool versions.
Wall-clock adjustments occurred during these particular runs. Their recorded
generation and TB stage durations plus the saved idle gap were used to correct
the duration to TB. The original calendar-derived value is retained in metadata.
Subsequent runs use a monotonic clock within one service session. Old missing
generation times remain unavailable.

rerun_report_experiments.py is the exact paid resampling script used for this
batch. Its project and evidence paths are installation-specific; do not run it
just to inspect the results. reconcile_report_records.py documents the known
clock correction and export procedure. render_report_windows.py is a local
report QA helper requiring the installed document renderer; it is not needed
to run the course workbench.

The report fills the AES work only. RISC-V workflow refactoring, its benchmarks
and its report section remain pending.
