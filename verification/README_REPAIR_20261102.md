# L3 修复复核 2026 年 11 月 2 日

当前普通 Run Agent 只允许 fresh：Parser、Planner 和 Coder 真实调用模型，禁止导入已成功 RTL。
从失败快照继续会标为 resumed。人工协助修复的候选单独存放，不进入默认生成路径。

## 当前状态

- 人工候选 `agent-20261102T005355596914/manual-repair-2` 通过教师 21 项测试、394710 次检查和独立压力 5 组、24198 次检查，L3 行为验证通过。
- 等待 0/3 拍的短程序另行通过。固定工具链 PPA：面积 245154.3419，功耗 0.0319154150784 W。
- 两次真实模型续跑仍失败：一次重复旧假设，一次诊断输出重复导致 JSON 截断。
- 新增当前源码锚点校验、短诊断格式和最多三次解析/证据重试。45 项本地回归通过。
- 最新真实请求 `agent-20261102T023027886899` 被模型服务 HTTP 402 Insufficient Balance 拦截；未返回 usage，因此 tokens 未知，不能记成 0。
- 修复后的自主闭环尚未验证成功。不能把人工候选的通过记录用于证明自主探索稳定。

## 继续真实模型探索

余额恢复后，在 WSL 的 `/home/eda/projects/THU_AI_EDA_2026` 执行：

```bash
.venv/bin/python -m verification.run_l3 \
  --resume agent-20261102T022253327959 --resume-attempt 2 \
  --restart-stage tester --optimize 1 \
  --evidence /mnt/f/AI_EDA/repair-autonomous-20261102
```

此命令显式读取原失败快照，不读取人工修复 RTL。要从空目录检验全过程，使用工作台普通 Run Agent，或不带 `--resume` 运行 `verification.run_l3`。

## 本地复核

```bash
.venv/bin/python -m unittest discover -s verification -p 'test_*.py'
.venv/bin/python -m verification.retest_repair \
  runs/agent/agent-20261102T005355596914/manual-repair-2 --ppa
```

第二条只复核人工候选，不调用模型；每次创建独立验证目录，不覆盖原失败记录。PPA 是 gscl45nm 库面积与 RTL VCD 活动的前布局功耗估算。

证据目录 `F:\AI_EDA\repair-review-20261102` 包含逐调用参数与输入/输出 tokens CSV、失败运行、冻结源码、两个人工候选、教师输入哈希、源码同步校验和更新后的报告。API Key 不包含在证据导出中。
