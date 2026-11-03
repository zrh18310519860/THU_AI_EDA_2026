# 作业二 L3 复现说明

## 交付内容

改造后的固定流程为 Parser → Planner → Coder → Tester → Diagnoser → Patch Agent → Tester。
六个节点之外有固定 Spec 输入和无模型调用的候选选择器。界面流程图是执行流程的可视化，不是任意图调度引擎。

最终运行 ID 和被选 Tester 在 `L3-evidence/l3-summary.json` 的 `selected_run`、`selected_tester` 字段中。
最终源码在 `runs/agent/<selected_run>/project/rtl/`，不可变版本和 SHA-256 在 `artifacts/tested_manifest_*.json`。
`artifacts/candidate_selection.json` 记录每个候选和最终报告引用。不要使用失败运行最后留下的可变 RTL 作为最终交付。

## 已安装环境

Windows 源码目录是 `D:/大三/人工智能赋能数字电路设计/THU_AI_EDA_2026`。
运行环境是 WSL `AI-EDA-Ubuntu-22.04`，项目目录 `/home/eda/projects/THU_AI_EDA_2026`。
虚拟磁盘和大体积工具、运行归档位于 F:；本次没有在 C:/D: 安装大型软件。
工具为 Python 3.10、Icarus 11.0、Yosys 0.9、OpenSTA 3.1.0 和 gscl45nm.lib。

以下命令在 WSL 项目根目录执行；先 `cd /home/eda/projects/THU_AI_EDA_2026`。

```bash
.venv/bin/python -m unittest discover -s verification -p 'test_*.py'
.venv/bin/python -m verification.verify_selected_l3 <selected_run>
.venv/bin/python -m verification.verify_selected_l3 <selected_run> --ppa
.venv/bin/python -m verification.check_cpu <selected_run> --units
.venv/bin/python -m verification.check_cpu <selected_run> --rename
```

`verify_selected_l3` 不调用模型，校验源码哈希后，重新运行未修改的教师 L3 TB 和独立压力测试。
`--ppa` 额外运行固定 800 周期功耗程序以及真实 Yosys/OpenSTA，可能耗时数分钟。
独立重新测试保存在新目录，不覆盖历史测量。

## 重新运行模型工作流

```bash
.venv/bin/python -m verification.run_l3 --optimize 3
.venv/bin/python -m verification.run_l3 --resume <run_id> --restart-stage tester --optimize 3
.venv/bin/python -m verification.run_l3 --resume <run_id> --resume-attempt 2 --restart-stage diagnoser --optimize 3
.venv/bin/python -m ai_eda serve --host 127.0.0.1 --port 8000
```

第一条是新的付费模型生成，结果不保证与历史相同；后两条显式续跑指定版本。
API Key 从本地配置或环境变量解析，不包含在压缩包中。模型是课程配置的 deepseek-flash。
不要把全局参数误认为每次调用的实际参数：Coder、Diagnoser 和 Patch Agent 有覆盖值，均保存在 `events.json`。
实际消耗以 API usage 为准；缺失 usage 不作字符数估算。中断运行也保留已完成调用。

## 修复与候选规则

- 每次测试前保存内容寻址的不可变源码；handoff 传引用，不传全部日志。
- failure packet 保留去重检查、首尾与故障附近 trace、有限逐退休 ISA 比对以及综合内存展开热点。
- Diagnoser 最多选 4 个实际需要修改的文件；Patch Agent 只允许带哈希的精确锚点修改，拒绝过期、越界与无操作补丁。
- 实验中修正过 Planner 接口、前端取消协议、自己的补充程序长度和综合瓶颈，属于有人工指导的迭代，不声称一次性完全自主生成成功。
- 所有候选必须通过教师测试、独立压力测试、目标 L3 行为验证和有效 PPA，才比较面积乘总功耗；失败或变差时回到最优快照。
- 没有修改教师 Spec/TB；前后哈希相同。不得删除失败记录、伪造性能事件或放宽测试预算。

## 测量边界

面积是相同 gscl45nm Liberty 的映射库面积单位，不是布局后面积。
功耗是 10 ns 时钟、固定教师活动程序和 RTL VCD 驱动的前布局估算，没有寄生、CTS 或时序收敛证明。
VCD 只注释能匹配的映射引脚；其他活动使用传播或默认值，微小差异应谨慎解释。
benchmark 是课程教学内核，不是官方 CoreMark；没有同条件的低等级对照时不报告跨等级加速比。
报告的运行耗时不包含人工排查和文档编写时间。R3 外部中断，准确端到端耗时未保存，不参与时间比较；原始记录中的步骤求和值可能包含中断到补记的间隔，导出表以 `duration_uncertain` 标注，不能当作准确实测。已完成调用的 API usage 仍完整。

完整试验、候选、模型调用和 benchmark 表位于 `L3-evidence/`，报告与归档都不含密钥。
