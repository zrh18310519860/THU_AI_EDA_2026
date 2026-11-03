# AI EDA 课程作业提交

本仓库提交 AES 128 Prompt 设计与 L3 多智能体工作流重构。最终报告位于 `report/`；完整原始运行记录见 [课程提交 Release](https://github.com/zrh18310519860/THU_AI_EDA_2026/releases/tag/course-submission-20261103)。Release 标明源码 commit、附件大小和 SHA-256。

## 阅读入口

- `report/`：按课程模板填写的最终实验报告。
- `submission/aes/`：作业一最终 A15 的 Prompt、生成 RTL、教师测试和元数据。
- `submission/l3/rtl/`：作业二最终 `agent-20261103T081144962755 / tester-17` 的不可变 RTL。
- `submission/l3/`：源码哈希、教师与独立测试报告、来源链和容量检查。
- `completion/`：34 次运行、1197 次真实 HTTP 请求的参数、token、耗时、候选 PPA、来源审计和历史工具版本。
- `verification/README_COMPLETION.md`：详细工作流、完整证据包目录和原始运行复现方法。

## 实测结果

| 作业 | 最终功能结果 | 面积（课程库单位） | 总功耗（mW） |
| --- | --- | ---: | ---: |
| AES 128 双 S-box，A15 | 教师 5 向量及 100 组独立随机对照通过 | 12164.7253 | 6.3747 |
| L3，tester-17 | 教师、L3 行为、六组补充压力、独立复测通过 | 324185.8698 | 23.83058145642 |

L3 由空目录真实生成后，经明确标记的续跑完成自动修复与优化。所有 RTL、接受的修复和优化来自真实模型调用；来源链见 `completion/completion.json`。这不是单次从零成功，最新额外探索仍有失败，不能声称每次生成都通过。原有教学参考实现保留在 `content/agent/skills/`，不参与正常从零运行，也不是上述最终 RTL 的来源。

L3 记录合计已知 50,225,836 tokens，另有 16 次请求缺 usage，精确总量未知。具体请求参数、角色覆盖和耗时见 `completion/model-calls.csv`。缺失 usage 不计为零。PPA 是固定教师程序与时钟下的综合及前布局估计，条件和局限见报告。

## 本仓库直接复测

先按 [环境准备](环境准备.md) 配置 Linux 或 WSL、Icarus、Yosys、OpenSTA，并安装 Python 依赖。本次测量使用 Python 3.10.12、Icarus 11.0、Yosys 0.70+post 和 OpenSTA 3.1.0；完整版本见 `completion/completion.json`。更换工具版本可能改变 PPA。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s verification -p 'test_*.py'
.venv/bin/python -m verification.verify_submission
.venv/bin/python -m verification.verify_submission --ppa
```

复测不会调用模型。它先核对最终 RTL 的 SHA-256，再运行原教师 TB、补充 L3 压力测试，以及可选 PPA。新结果写入 `submission/retest/`，不覆盖历史证据。

## 运行工作台与重新探索

```bash
.venv/bin/python -m ai_eda serve --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/`。正常 Run Agent 从空目录启动；续跑必须显式选择父运行。模型为 `DeepSeek-V4.1-Flash-1017`，API 基址为 `https://llmapi.paratera.com/v1`。凭据仅在本地配置，重新生成会产生 API 费用。工作流为 Parser → Planner → Coder → Tester → Diagnoser → Patch Agent → Tester；功能和 PPA 通过后按面积与功耗乘积保留最佳候选。

## 完整记录

从 Release 下载并解压完整证据包。包中 `THU_AI_EDA_2026/runs/` 包含模型请求与回答、补丁、不可变源码、教师测试和 PPA 日志，`manifest-sha256.json` 可逐文件核验。将包中的 `runs/` 放回本仓库根目录后，可按详细复现说明复测原始运行 ID。

教师 Spec、TB 和评分条件保持原样；API Key、虚拟环境、大型工具与 Office 临时锁文件不进入本次提交。
