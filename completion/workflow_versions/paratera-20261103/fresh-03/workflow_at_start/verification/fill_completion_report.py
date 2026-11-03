"""Fill the retained course template from verified measurements; preserve AES content."""

import argparse
import json
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt
from PIL import Image, ImageDraw, ImageFont

from verification.fill_l3_report import cell_text, rows, widths, paragraph_after
from verification.fill_stability_report import finish, text


BASE = Path(r"D:\大三\人工智能赋能数字电路设计")


def flow_diagram(path):
    image = Image.new("RGB", (1440, 470), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 27)
    small = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 20)
    positions = [("Parser", 20, 40), ("Planner", 330, 40), ("Coder", 640, 40),
                 ("Tester", 950, 40), ("Diagnoser", 950, 310), ("Patch Agent", 640, 310)]
    for label, x, y in positions:
        draw.rectangle((x, y, x + 220, y + 80), outline="#333333", fill="#EDF2F5", width=3)
        draw.text((x + 110, y + 40), label, font=font, fill="black", anchor="mm")
    def arrow(points):
        draw.line(points, fill="black", width=3)
        (a, b), (x, y) = points[-2:]
        if x > a:
            triangle = [(x, y), (x-12, y-7), (x-12, y+7)]
        elif x < a:
            triangle = [(x, y), (x+12, y-7), (x+12, y+7)]
        elif y > b:
            triangle = [(x, y), (x-7, y-12), (x+7, y-12)]
        else:
            triangle = [(x, y), (x-7, y+12), (x+7, y+12)]
        draw.polygon(triangle, fill="black")
    for x, label in [(240, "spec"), (550, "plan"), (860, "manifest")]:
        arrow([(x, 80), (x + 90, 80)])
        draw.text((x+45, 50), label, font=small, fill="black", anchor="mm")
    arrow([(1060, 120), (1060, 310)])
    draw.text((1080, 200), "failure packet / PPA", font=small, fill="black")
    arrow([(950, 350), (860, 350)])
    draw.text((900, 410), "repair brief", font=small, fill="black", anchor="mm")
    arrow([(640, 350), (530, 350), (530, 210), (900, 210), (900, 100), (950, 100)])
    draw.text((520, 440), "immutable patched snapshot -> full retest", font=small, fill="black", anchor="mm")
    draw.text((1210, 60), "PASS + PPA", font=small, fill="black")
    draw.text((1210, 90), "retain best", font=small, fill="black")
    image.save(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("summary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.summary.read_text(encoding="utf-8"))
    report = data["report"]
    assert report["passed"] and report["ppa_passed"] and data["independent_retest"]["passed"]
    selected = data["selected_run"]
    chosen = next(item for item in data["records"] if item["run_id"] == selected)
    assert chosen["generation_mode"] == "fresh" and chosen["status"] == "passed"
    source = BASE / "作业提交要求与报告模板_AES128补跑记录.docx"
    doc = Document(source)
    tables = list(doc.tables)
    metrics = report["metrics"]
    text(doc.paragraphs[0], "AI EDA 课程作业实验报告")
    doc.paragraphs[0].style = "Title"
    text(doc.paragraphs[1], "AES 128 Prompt 与 L3 多智能体设计")
    text(doc.paragraphs[2],
        "作业一保留原有 AES 128 Prompt 和实测记录。本次完成作业二的工作流重构，真实调用指定模型从空目录规划并生成 L3 CPU，经过自动诊断与补丁回环，通过教师测试、补充压力测试及独立复测，再比较面积和功耗。历史失败与续跑单独记录，不作为从零成功。")
    cell_text(tables[0].cell(2, 3), "2026-11-03")
    cell_text(tables[0].cell(4, 1), "完整工程、报告和运行证据压缩包；详见本次提交目录及 README_COMPLETION.md")
    cell_text(tables[0].cell(4, 3), "Windows + WSL Ubuntu 22.04；工具链和版本见 completion 证据；大型工具及归档位于 F 盘")
    rows(tables[6], [["L0 基础五级流水线", "未选", "N/A", "N/A"],
        ["L1 I D Cache", "未选", "N/A", "N/A"], ["L2 Cache 加分支预测", "未选", "N/A", "N/A"],
        ["L3 Cache 预测加乱序", "已选", "rv32i5_ooo", "PASS；独立复测 PASS；行为验证 L3"]])
    diagram = args.summary.parent / "agent-flow.png"
    flow_diagram(diagram)
    cell_text(tables[7].cell(0, 0), "实际实现的六节点工作流", 10)
    tables[7].cell(0, 0).add_paragraph().add_run().add_picture(str(diagram), width=Inches(6.05))
    cell_text(tables[8].cell(0, 0),
        "正常 Run Agent 从空目录启动，不读取内置 CPU。Parser 冻结规格，Planner 生成并复审模块契约，Coder 按依赖逐文件生成并做局部编译。Tester 保存不可变源码、哈希和报告；失败后进入 Diagnoser → Patch Agent → Tester。\n"
        "failure packet 保留失败阶段、去重检查、首个偏差、按复位分段的有限波形及原始 artifact 引用。诊断器一次最多查看 8 个文件、共 96000 字符，可按需读取 5 批；只允许选择最多 4 个待修改文件。旧假设明确标为历史，最多保留最近 3 轮。\n"
        "repair brief 给出当前源码证据、根因、修改范围、保留约束和验收目标。补丁器只接收相关代码及接口，输出精确锚点 JSON；校验哈希、锚点唯一性、路径、无操作补丁和编译后才写入。格式重试不累积全部历史。\n"
        "冒烟 → 原教师 TB → 独立压力测试 → PPA 逐级验收。只有完整功能和 PPA 均通过的候选，才能按面积×总功耗比较。每次优化从最佳已验证快照开始；回归或无效提案不覆盖最佳版本。", 9.5)
    agent_rows = [
        ["Parser", "检查规格是否充分", "原教师 Spec", "effective_spec.md", "真实请求；继承 thinking on/low，32000"],
        ["Planner", "规划并复审端口与并发协议", "有效 Spec", "plan.json", "草案 off/24000；复审 on/low/24000；截断降级另记"],
        ["Coder", "按依赖逐文件生成", "模块契约及本轮依赖片段", "RTL manifest 及生成来源", "thinking off，T=0.2，24000；Icarus/Yosys 局部检查"],
        ["Tester", "编译、功能、L3 行为及 PPA", "不可变 RTL 与原教师 TB", "test_report 与 failure packet", "不调用模型；工具实测决定 PASS"],
        ["Diagnoser", "追踪第一个偏差", "受限证据和按需源码", "repair_brief.json", "thinking on/low，24000；当前锚点；重复有效证据归并"],
        ["Patch Agent", "定点修改相关文件", "repair brief、源码哈希及契约", "patch 和新 manifest", "thinking off，T=0.2，16000；校验失败最多 3 次"],
        ["候选选择器", "保留通过且乘积更小者", "功能、能力和实测 PPA", "candidate_selection.json", "非模型节点；不以代码长短预测 PPA"]]
    rows(tables[9], agent_rows)
    widths(tables[9], [.7, 1.05, 1.2, 1.2, 1.95])
    labels, run_rows = {}, []
    for index, item in enumerate(data["records"], 1):
        label = f"R{index}"
        labels[item["run_id"]] = label
        candidates = [c for c in data["candidates"] if c["run_id"] == item["run_id"] and c["selected_in_run"] and c["ppa_passed"]]
        current = candidates[0]["metrics"] if candidates else None
        usage = item["usage"]
        tokens = f"{usage['total_tokens']:,}" if usage["usage_complete"] else f"已知 {item['known_usage']['total_tokens']:,}；缺 {usage['missing_usage_calls']} 次"
        mode = "从零" if item["generation_mode"] == "fresh" else "续跑"
        run_rows.append([label, "L3 " + mode, item["status"].upper(),
            f"{current['area']:.1f}" if current else "N/A", f"{current['total_power_w']*1000:.4f}" if current else "N/A",
            tokens, f"{item['duration_ms']/1000:.2f} s" if item["duration_ms"] is not None else "缺失",
            item["run_id"] + ("\n" + item["error"] if item.get("error") else "")])
    run_rows.append(["最终采用", "L3 从零", "PASS", f"{metrics['area']:.1f}", f"{metrics['total_power_w']*1000:.4f}",
        "计入对应运行", "计入对应运行", f"{labels[selected]} / {data['selected_tester']}；独立复测通过"])
    rows(tables[10], run_rows)
    widths(tables[10], [.45, .55, .5, .75, .7, .85, .75, 1.55])
    paragraph_after(doc, tables[10]._tbl,
        f"模型为 DeepSeek-V4.1-Flash-1017，网关为 Paratera。全局 thinking on/low、max_tokens=32000，调用上限250、修复上限40、优化3轮；实际角色覆盖参数和每次重试见 model-calls.csv。表中 token 是该运行全部请求的 API 输入加输出累计，耗时是包含工具的运行墙钟时间，不含人工排查及报告编写。缺 usage 的调用不计为0。本次记录已知 {data['known_tokens']:,} tokens，另有 {data['missing_usage_calls']} 次缺 usage。面积单位为课程标准单元库面积单位；总功耗单位 mW。")
    selected_candidates = [c for c in data["candidates"] if c["run_id"] == selected and c["ppa_passed"]]
    baseline = selected_candidates[0]["metrics"]
    heading = paragraph_after(doc, tables[10]._tbl, "同一运行的优化结果", "Heading 2")
    detail = doc.add_table(rows=1, cols=5)
    heading._p.addnext(detail._tbl)
    for cell, value in zip(detail.rows[0].cells, ["候选", "功能与 PPA", "面积", "总功耗 mW", "是否最终采用"]):
        cell_text(cell, value)
    rows(detail, [[c["tester"], "PASS" if c["ppa_passed"] else "FAIL",
        f"{c['metrics']['area']:.4f}" if c["ppa_passed"] else "N/A",
        f"{c['metrics']['total_power_w']*1000:.6f}" if c["ppa_passed"] else "N/A",
        "采用" if c["tester"] == data["selected_tester"] else "保留证据，未最终采用"]
        for c in data["candidates"] if c["run_id"] == selected])
    widths(detail, [.8, .9, 1.1, 1.1, 2.1])
    names = {"trained_branch64": "热分支训练64", "teaching_crc32_64bytes": "CRC32 64字节", "teaching_array_sum64": "数组求和64",
             "teaching_branch_predict64": "混合分支64", "teaching_ooo_overlap64": "乱序访存重叠64"}
    rows(tables[11], [[names.get(b["benchmark"], b["benchmark"]), "PASS", b["cycles"], f"{b['cpi']:.4f}",
        f"{b.get('mispredicts', 0)}/{b.get('branches', 0)}", b.get("ooo_bypass", "N/A"),
        f"退休 {b['instructions']}；IPC={b['ipc']:.3f}"] for b in report["performance"]["benchmarks"]])
    widths(tables[11], [1.2, .5, .6, .65, .8, .65, 1.5])
    area_gain = (1 - metrics["area"] / baseline["area"]) * 100
    power_gain = (1 - metrics["total_power_w"] / baseline["total_power_w"]) * 100
    cell_text(tables[12].cell(0, 0),
        f"最终采用 {selected}/{data['selected_tester']}；目标、声明和行为验证均为 L3。教师、补充及独立复测报告均通过。相对该运行首个有效候选，面积下降 {area_gain:.2f}%，总功耗下降 {power_gain:.2f}%。全部优化候选与被拒提案保留，不把失败候选的数值用于排名。\n"
        "真实结构包括 8 项 ROB、8 项整数发射队列、40 个物理寄存器及至少 2 项 LSQ，保留 I/D Cache、动态预测、乱序执行与顺序提交。较老 load 等待时年轻独立 ALU 的发射由行为测试验证，不以能力标志替代。benchmark 为课程教学内核；无同条件低等级实现，不报告跨等级加速比。\n"
        "工作流改进来自对失败日志的检查：按实际信号名提取波形，按复位和等待模式分段；补齐操作数、写回、访存和提交链；保留最早偏差；允许读取缺失的当前模块；将历史假设与当前证据分开。诊断恢复推理预算后能够追踪跨模块时序，补丁仍关闭思考并限制范围。\n"
        "人工修改仅限通用 Agent 代码、提示、诊断工具和记录导出。最终从零实验中的候选 RTL、修复及优化由真实 API 生成；来源审计复核初始回答、每个接受补丁和不可变快照。此前人工辅助候选不作为本次成功来源。\n"
        "PPA 使用固定课程标准单元库、功耗程序和时钟约束，属于综合及前布局功耗估算，不包含布局寄生、时钟树或时序收敛证明。有限测试的通过也不证明任意 RV32I 程序均正确。\n"
        f"证据见提交包 completion/：completion.json、model-calls.csv、candidates.csv、benchmarks.csv、来源审计和完整 runs；独立复测位于 {data['independent_report']}。复现命令见 README_COMPLETION.md。教师文件的哈希与 Git HEAD 及运行前记录一致；API Key 不进入提交包。", 9.5)
    finish(doc, [*tables, detail])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(args.output)
    for old, new in zip(Document(source).tables[1:6], Document(args.output).tables[1:6]):
        assert [[c.text for c in r.cells] for r in old.rows] == [[c.text for c in r.cells] for r in new.rows]
    print(args.output)


if __name__ == "__main__":
    main()
