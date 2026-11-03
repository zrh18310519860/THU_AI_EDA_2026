# L3 多智能体作业复现说明

本说明对应 2026 年 11 月 3 日之后导出的 completion 证据。具体是否通过、最终运行和候选编号，以 `completion/completion.json` 为准。历史 README 和旧报告记录早期实验，不代表本次结果。

## 查阅提交材料

- `report/`：按课程模板填写的报告，保留作业一 AES 实测内容。
- `completion/completion.json`：最终运行、候选、教师验收、独立复测、工具版本及教师文件哈希。
- `completion/model-calls.csv`：每个实际 HTTP 请求的模型、参数、输入输出 token、耗时和重试记录。
- `completion/runs.csv`：各次实验的从零或续跑模式、结果、墙钟时间和累计消耗。
- `completion/candidates.csv`：包括失败在内的每轮候选与实测 PPA。
- `completion/benchmarks.csv`：教师教学程序的周期、CPI、IPC 和能力事件。
- `completion/workflow_versions/`：匹配到的每次实验启动时工具源码与 SHA-256；历史版本不冒充最终代码。
- `THU_AI_EDA_2026/runs/agent/<selected_run>/`：完整原始调用、消息、工具日志、源码快照和测试报告。
- `manifest-sha256.json`：压缩包各文件的 SHA-256。

`selected_run` 和 `selected_tester` 定位最终候选。最终源码位于该运行的 `project/rtl/`；不可变快照由 `artifacts/tested_manifest_N.json` 指向。每次补丁的输入、精确修改、哈希和模型原始回答均保留。不能用失败运行最后留下的源码替代最终候选。

## 环境和模型

运行发行版为 WSL `AI-EDA-Ubuntu-22.04`，工程位于 `/home/eda/projects/THU_AI_EDA_2026`，Windows 编辑副本位于 `D:/大三/人工智能赋能数字电路设计/THU_AI_EDA_2026`。WSL 虚拟磁盘、大型工具和证据归档位于 F 盘。工具的实际版本由 completion 导出命令采集。

本次模型为 `DeepSeek-V4.1-Flash-1017`，API 基址为 `https://llmapi.paratera.com/v1`。凭据从本地 `.api_key` 或环境配置解析，不进入报告和提交包。

界面正常 Run Agent 从空目录开始；续跑必须显式选择原运行和快照，并标记 `resumed`。默认调用上限 250，修复上限 40，功能通过后最多尝试 3 轮优化。模型具有随机性，历史成功不保证新生成一定成功。

全局参数与角色的实际覆盖参数不同，不能只抄运行配置。每个请求的最终参数和 usage 见 `model-calls.csv` 以及各步骤 `call-NNN/request_records.json`。缺失 usage 标为未知，不以零或字符数代替；运行耗时是包括工具在内的墙钟时间，不包括人工排查和报告编写。并行实验的耗时相加表示累计运行时长，不表示实际等待时间。

## 工作流

Parser 解释教师规格；Planner 规划并复审模块接口和时序合同；Coder 按依赖生成文件并做局部编译及辅助单模块行为验证。辅助 TB 由模型根据接口合同生成，发现采样或握手问题时先复核测试；测试冻结后再修复模块，只有辅助测试通过的修订才进入正式候选。辅助测试不可用或未通过会如实记录，仍须整机验收。旧运行的工具版本和处理策略按原记录保留。Tester 保存不可变快照，再执行冒烟、原教师 TB、补充压力测试和面积功耗评估。

失败时，Diagnoser 接收去重检查、最早偏差、有限波形窗口和 artifact 引用，按需读取当前源码；最近三轮假设仅作历史参考。源码窗口最多 8 个文件、96000 字符，最多读取 5 批，最终修改范围最多 4 个文件。Patch Agent 根据带当前哈希的 repair brief 输出精确锚点补丁；通过路径、哈希、锚点和编译校验后才形成下一候选。禁止回退到内置 CPU 或人工 RTL。

若拟修改文件在按需读取过程中被挤出窗口，证据校验会指出具体缺失路径，并在格式重试前重新优先提供这些文件的当前源码。该操作保持窗口上限，不自动生成引用；只有模型返回可验证的当前源码锚点才接受。对应回归覆盖文件被挤出、重载后的顺序及大小边界。

JSON 格式错误和证据或结构错误分别最多三次，最多五批按需读取，调用总数仍有明确上限。平台只返回不完整 JSON 时，不占用证据修正次数；每个请求依旧单独计量。`contexts.csv` 另列源码重载和校验重试，历史运行没有的字段保持空列表。

诊断或独立复核若返回空的 `changes`，最终修改器会在调用付费模型及写入候选之前拒绝执行，并由调度器重新诊断。它不能将“证据不足，暂不修改”擅自解释成修改授权；该规则来自真实失败交接的复核，早期运行仍保留原始行为。

最终代码对功能诊断增加一次独立模型复核，使用当前证据和待修模块，不复制历史假设；实际参数为 thinking on/low、max_tokens=16000。初诊为 thinking off、temperature=0.1、max_tokens=8000。该策略的实际启用情况须看对应运行的逐调用记录；启用前已经完成的候选不补记调用。网关错误按真实失败记录，未返回 usage 时保持未知。

只有完整功能、L3 行为和 PPA 均通过的候选才比较 `area * total_power_w`。每轮优化从最佳已验证快照开始，并传递此前实测结果及补丁摘要。缩减既定检查点容量的提案会被拒绝；源码 token 指纹用于拦截仅改注释或排版的重复优化。回归候选和无效提案保留证据，不覆盖最佳版本。

## 不调用模型的验收

以下命令在 WSL 工程根目录执行。先从 completion 读取实际的最终运行 ID，替换 `<selected_run>`。

```bash
cd /home/eda/projects/THU_AI_EDA_2026
.venv/bin/python -m unittest discover -s verification -p 'test_*.py'
.venv/bin/python -m verification.verify_selected_l3 <selected_run> --ppa
```

独立复测先检查不可变源码哈希，再运行原教师测试、补充压力测试及 Yosys/OpenSTA。结果写入新的 `independent_retest/时间戳/`，不会覆盖此前测量，也不调用模型。

最新补充测试共有六组：WAR 与队列压力、检查点复用及错路径 store/ECALL、检查点与较老提交并发、随机种子 17/2026/1948 对应等待 0/3/12 拍。新增压力测试可能推翻历史有限测试的 PASS；报告保留原结果，并另记最新复测失败。最终候选必须通过最新完整复测。

## 重新从零探索

以下命令会调用付费模型，产生新实验记录。

```bash
.venv/bin/python -m verification.run_l3 --mode fresh --optimize 3 \
  --repair-attempts 40 --max-calls 250 \
  --evidence /mnt/f/AI_EDA/new-l3-experiment
.venv/bin/python -m ai_eda serve --host 127.0.0.1 --port 8000
```

来源审计命令会核对空目录起点、初始模型回答、每个接受补丁的重放结果、源码哈希和教师文件完整性。审计通过只证明来源可追溯，功能结果仍由测试决定。

```bash
.venv/bin/python -m verification.audit_fresh_run <selected_run> \
  --evidence /mnt/f/AI_EDA/new-l3-experiment
```

如果最终候选是续跑，使用 `audit_lineage` 逐级回溯到空目录起点，核对每个接受补丁和父子快照；它保持 resumed 标签，不把多个运行合并为单次 fresh：

```bash
.venv/bin/python -m verification.audit_lineage <selected_run> \
  --evidence /mnt/f/AI_EDA/lineage-review \
  --origin-evidence /mnt/f/AI_EDA/original-fresh-evidence
```

## 测量边界

面积是课程标准单元库的综合映射面积，总功耗是固定教师活动程序、10 ns 时钟和 RTL VCD 驱动的前布局估算。不包含布局寄生、时钟树或时序收敛证明；未匹配的活动采用工具传播或默认值。教学 benchmark 不是官方 CoreMark，也没有同条件低等级对照可用于声称跨等级加速。

教师 Spec、TB 和预期值保持不变，补充测试只增加覆盖。通过有限测试不证明任意 RV32I 程序均正确。最终报告必须区分从零、续跑和失败实验；历史人工辅助候选不能充当本次从零成功来源。
