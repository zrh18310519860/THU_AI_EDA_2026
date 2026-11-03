"""Preserve the previous report and record assisted repairs separately from real agent runs."""

import csv
import hashlib
import json
import shutil
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt
from PIL import Image, ImageDraw, ImageFont
from verification.fill_l3_report import cell_text, rows, widths
from verification.fill_stability_report import finish, text

BASE = Path(r"D:\大三\人工智能赋能数字电路设计")
ROOT = Path(r"\\wsl.localhost\AI-EDA-Ubuntu-22.04\home\eda\projects\THU_AI_EDA_2026")
OUT = Path(r"F:\AI_EDA\repair-review-20261102")
TARGET = BASE / "作业提交要求与报告模板_L3修复复核_20261102.docx"
PARENT = "agent-20261102T005355596914"
RUNS = [PARENT, "agent-20261102T021907522700", "agent-20261102T022253327959",
        "agent-20261102T023027886899"]
CANDIDATES = [("manual-repair-1", "verified-20261102T021525986892"),
              ("manual-repair-2", "verified-20261102T022337293697")]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def draw_flow():
    image = Image.new("RGB", (1250, 440), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 27)
    small = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 20)
    for label, x, y in [("Parser", 50, 60), ("Planner", 350, 60), ("Coder", 650, 60),
                        ("Tester", 950, 60), ("Diagnoser", 950, 290), ("Patch Agent", 650, 290)]:
        draw.rectangle((x, y, x + 200, y + 90), fill="#EEF2F5", outline="#32383E", width=3)
        draw.text((x + 100, y + 45), label, font=font, fill="black", anchor="mm")
    def arrow(points):
        draw.line(points, fill="#32383E", width=3)
        (x0, y0), (x, y) = points[-2:]
        if x > x0:
            head = [(x, y), (x-12, y-7), (x-12, y+7)]
        elif x < x0:
            head = [(x, y), (x+12, y-7), (x+12, y+7)]
        elif y > y0:
            head = [(x, y), (x-7, y-12), (x+7, y-12)]
        else:
            head = [(x, y), (x-7, y+12), (x+7, y+12)]
        draw.polygon(head, fill="#32383E")
    for x in (250, 550, 850):
        arrow([(x, 105), (x+100, 105)])
    arrow([(1050, 150), (1050, 290)])
    draw.text((1070, 215), "failure / optimize", font=small, fill="black", anchor="lm")
    arrow([(950, 335), (850, 335)])
    arrow([(650, 335), (590, 335), (590, 210), (900, 210), (900, 130), (950, 130)])
    draw.text((680, 230), "retest snapshot", font=small, fill="black")
    path = OUT / "agent-flow.png"
    image.save(path)
    return path


def export_evidence():
    OUT.mkdir(parents=True, exist_ok=True)
    candidates = []
    for name, verification in CANDIDATES:
        source = ROOT / "runs/agent" / PARENT / name / verification
        target = OUT / name
        target.mkdir(exist_ok=True)
        for filename in ("summary.json", "test_report.json", "candidate.sv"):
            shutil.copy2(source / filename, target / filename)
        shutil.copytree(source / "rtl", target / "rtl", dirs_exist_ok=True)
        report = read(source / "test_report.json")
        summary = read(source / "summary.json")
        summary.update(label=name, exported_report=str(target / "test_report.json"))
        candidates.append(summary)
    records, calls = [], []
    for run_id in RUNS:
        source = ROOT / "runs/agent" / run_id
        payload = read(source / "events.json")
        summary = read(source / "run_summary.json")
        target = OUT / "agent-runs" / run_id
        target.mkdir(parents=True, exist_ok=True)
        for filename in ("events.json", "metadata.json", "run_summary.json"):
            shutil.copy2(source / filename, target / filename)
        records.append({"run_id": run_id, "status": payload["status"],
                        "resumed_from": payload.get("resumed_from"), **summary,
                        "error": payload.get("error")})
        for event in payload.get("events", []):
            for index, call in enumerate(event.get("model_calls", []), 1):
                usage = call.get("usage") or {}
                calls.append({"run_id": run_id, "step": event["step"], "node": event["node_id"],
                    "retry": index, **{key: call.get(key) for key in
                        ("model", "temperature", "max_tokens", "thinking", "reasoning_effort",
                         "status", "started_at", "finished_at", "duration_ms", "http_status", "finish_reason")},
                    **{key: usage.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")}})
        for manifest in sorted((source / "artifacts").glob("tested_manifest_*.json")):
            snapshot = read(manifest)
            snapshot_dir = target / manifest.stem
            for entry in snapshot["files"]:
                destination = snapshot_dir / entry["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / entry["artifact"], destination)
    with (OUT / "model-calls.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(calls[0]))
        writer.writeheader()
        writer.writerows(calls)
    teacher = ROOT / "content/tasks/homework_rv32i5"
    current = {str(p.relative_to(teacher)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(teacher.rglob("*")) if p.is_file()}
    baseline = candidates[-1]["provenance"]["teacher_hashes"]
    integrity = {"unchanged": current == baseline, "hashes": current}
    assert integrity["unchanged"], "Teacher inputs changed since verification"
    data = {"agent_runs": records, "candidates": candidates, "teacher_integrity": integrity,
            "regression_tests": 45, "autonomous_success_verified": False,
            "resume_command": ".venv/bin/python -m verification.run_l3 --resume agent-20261102T022253327959 --resume-attempt 2 --restart-stage tester --optimize 1 --evidence /mnt/f/AI_EDA/repair-autonomous-20261102"}
    (OUT / "summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data, report


def main():
    data, report = export_evidence()
    doc = Document(BASE / "作业提交要求与报告模板_稳定性复核版.docx")
    tables, paragraphs = list(doc.tables), list(doc.paragraphs)
    first, best = data["candidates"]
    a, b = first["metrics"], best["metrics"]
    text(paragraphs[0], "AI EDA 课程作业实验与 L3 修复复核报告")
    paragraphs[0].style = "Title"
    for run in paragraphs[0].runs:
        run.font.size = Pt(16)
        run.font.bold = True
    text(paragraphs[1], "2026 年 11 月 2 日整理版")
    text(paragraphs[2], "作业一沿用 10 月 27 日双 S-box 实测记录。本次更新作业二：修复重复错误诊断、补丁交接和电路本身的问题。人工协助修复的 L3 候选通过教师及补充测试，并完成两次面积功耗评估。真实多智能体续跑仍未通过，最新请求因模型服务余额不足中断，不能据此宣称从零自主探索已稳定成功。旧报告与失败记录均保留。")
    cell_text(tables[0].cell(2, 3), "2026-11-02")
    for paragraph in tables[2].cell(0, 0).paragraphs:
        paragraph.paragraph_format.line_spacing = 1.0
        for run in paragraph.runs:
            run.font.size = Pt(9.5)
    cell_text(tables[0].cell(4, 1), "当前源码 D:\\大三\\人工智能赋能数字电路设计\\THU_AI_EDA_2026\n本次证据 F:\\AI_EDA\\repair-review-20261102\n作业一原压缩包另行保留")
    cell_text(tables[6].cell(4, 3), "人工候选 PASS\n真实智能体续跑未通过")
    cell_text(tables[7].cell(0, 0), "六个子 Agent 的固定流程，当前默认从空目录探索。", 10)
    tables[7].cell(0, 0).add_paragraph().add_run().add_picture(str(draw_flow()), width=Inches(6.05))
    cell_text(tables[8].cell(0, 0),
        "Parser → Planner → Coder → Tester；失败后进入 Diagnoser → Patch Agent → Tester。Run Agent 默认 fresh，真实调用模型规划并逐模块生成，禁止导入内置成功实现。显式恢复旧快照标为 resumed，人工修复候选单独标为 human_assisted_repair。\n"
        "Tester 冻结源码和 SHA-256，再做等待 0/3 拍冒烟、教师测试、独立压力测试；功能满足后才评估 PPA。完整日志保留在文件中，交接只传 artifact 引用、去重失败、首尾轨迹和有限源码。\n"
        "Diagnoser 读取当前快照，旧假设单独标注；每个拟修改文件必须引用一段当前源码。最多四个文件，每文件仅一条短证据。无效或截断 JSON、过期证据最多重试三次；不累积整个历史。Patch Agent 校验源码哈希和唯一锚点，再原子应用补丁。内部接口如需协同修复，必须同时包含生产者、消费者和所有实例化位置。\n"
        "结构审计补充缓存总位宽、初始物理寄存器就绪、无效队列完成信号等线索，不替代功能验收。连续相同故障会停止盲修。功能、L3 行为和 PPA 都有效后，才以面积×总功耗选择候选；失败候选不覆盖已验证版本。", 9.5)
    flow_cell = tables[8].cell(0, 0)
    notes = flow_cell.text.splitlines()
    flow_cell.text = ""
    for index, note in enumerate(notes):
        paragraph = flow_cell.paragraphs[0] if index == 0 else flow_cell.add_paragraph()
        text(paragraph, note)
        paragraph.paragraph_format.keep_together = True
        paragraph.paragraph_format.space_after = Pt(5)
        for run in paragraph.runs:
            run.font.size = Pt(9.5)
    cell_text(tables[9].cell(2, 1), "真实模型规划模块、端口、所有权和并发协议")
    cell_text(tables[9].cell(2, 4), "默认 fresh；固定教师外部接口，明确 ROB/IQ/LSQ 反压、完成仲裁与恢复时机")
    cell_text(tables[9].cell(3, 1), "按依赖逐模块生成并局部编译，顶层最后连接")
    cell_text(tables[9].cell(3, 4), "禁止内置 RTL；记录每个文件的调用来源；生成约束含容量、就绪、提交和算术移位")
    cell_text(tables[9].cell(5, 4), "T=0.2，thinking=off，max_tokens=4000；一文件一条当前证据；格式错误最多三次调用")
    cell_text(tables[9].cell(6, 4), "T=0.2，thinking=off，max_tokens=16000；校验重试只携带当前源和最近被拒补丁")
    text(paragraphs[28], "下表 A0 为原失败，A1 至 A3 均为显式快照续跑，未导入人工修复 RTL。全局为 deepseek-flash、thinking=on/low、max_tokens=32000、max_calls=80、最多修复10轮、优化1轮。子 Agent 实际覆盖参数以 model-calls.csv 为准：诊断早期为8000，当前为4000，补丁为16000，二者均关闭思考、T=0.2。tokens 使用 API 输入加输出总量；缺失 usage 保留为空。M1/M2 是人工协助修复后的本地测试，0 tokens 仅指这两次验证未调用模型，不代表之前探索没有成本。")
    notes = ["原始失败快照，保留原记录", "修正数据容量后仍失败；旧假设反复出现", "修正 tag 后诊断输出重复并截断 JSON", "模型请求被 HTTP 402 余额不足拦截"]
    values = []
    for i, item in enumerate(data["agent_runs"]):
        tokens = item.get("total_tokens")
        duration = item.get("duration_ms")
        values.append([f"A{i}", "L3", "FAIL", "N/A", "N/A",
            f"{tokens:,}" if tokens is not None else "未返回 usage",
            f"{duration/1000:.2f} s" if duration is not None else "未记录", notes[i]])
    for i, item in enumerate(data["candidates"], 1):
        metrics = item["metrics"]
        values.append([f"M{i}", "L3", "PASS", f"{metrics['area']:.1f}",
            f"{metrics['total_power_w']*1000:.4f}", "0 本地验证",
            f"{item['provenance']['duration_ms']/1000:.2f} s", "人工协助修复；含完整功能与 PPA 耗时"])
    rows(tables[10], values)
    widths(tables[10], [.45, .35, .5, .85, .8, .85, .8, 1.7])
    text(paragraphs[29], "人工修复候选的面积功耗比较")
    reduction_area = (1 - b["area"] / a["area"]) * 100
    reduction_power = (1 - b["total_power_w"] / a["total_power_w"]) * 100
    text(paragraphs[30], f"M1 完成电路修复后，M2 将 PRF 和检查点银行的写入改为每项固定编号的 generate 结构，降低动态宽写入选择开销。容量和外部行为不变。面积下降 {reduction_area:.2f}%，总功耗下降 {reduction_power:.2f}%。这是同工具链的人工协助优化对照，不能计作真实多智能体独立优化成功。")
    rows(tables[11], [[f"M{i}", "PASS", f"{item['metrics']['area']:.4f}",
        f"{item['metrics']['total_power_w']*1000:.6f}",
        f"{item['metrics']['area']*item['metrics']['total_power_w']:.3f}",
        "人工修复对照" if i == 1 else "当前最佳人工候选"] for i, item in enumerate(data["candidates"], 1)])
    names = {"trained_branch64": "热分支训练64", "teaching_crc32_64bytes": "CRC32 64字节教学核",
        "teaching_array_sum64": "数组求和64", "teaching_branch_predict64": "混合分支64",
        "teaching_ooo_overlap64": "乱序访存重叠64"}
    rows(tables[12], [[names.get(x["benchmark"], x["benchmark"]), "PASS", str(x["cycles"]),
        f"{x['cpi']:.4f}", f"{x.get('mispredicts', 0)}/{x.get('branches', 0)}",
        str(x.get("ooo_bypass", "N/A")), f"退休 {x['instructions']}；IPC={x['ipc']:.3f}"]
        for x in report["performance"]["benchmarks"]])
    teacher_files = len(data["teacher_integrity"]["hashes"])
    cell_text(tables[13].cell(0, 0),
        f"M2 教师 TB 为 PASS，共21项测试、394710次检查；独立压力测试5组、24198次检查通过。目标、声明和行为验证均为 L3。两种外部存储等待条件的冒烟通过。教师 Spec/TB 的{teacher_files}个文件哈希未改变。\n"
        "电路根因包括：I-cache 多行数据和 tag 容量不足；前端响应与PC配对、反压不完整；LSU空槽仍输出旧完成信号，load错误依赖伪rs2；store提交与响应相互等待；D-cache用旧地址查找；ALU与LSU完成冲突；初始就绪位、空闲物理寄存器回绕、WAW提交映射和分支恢复错误；算术右移被表达式上下文转为逻辑移位。人工候选已逐项处理，并保持乱序能力。\n"
        "本次工作流修复：失败探针含X/Z的退休记录仍保存证据；有访存活动时不再报告零活动；后期PC/指令错误会检查配对和恢复链，不笼统归咎缓存。诊断引用当前源码、限制重复证据，并把JSON解析纳入有限重试。补丁重试保留最近被拒回答及具体校验错误。HTTP 402显示余额不足并停止，不盲目重试；缺失usage不记成0。45项本地回归测试通过。\n"
        "PPA条件为Yosys 0.70+post、OpenSTA、gscl45nm、10 ns。面积是库面积单位；功耗按映射网表和RTL VCD估算，未包含布局寄生和时钟树，不证明100 MHz时序收敛。M2总功耗31.915415 mW，其中动态30.795106 mW、漏电1.120310 mW。\n"
        "真实探索结论仍为未完成：A1/A2只修复部分问题，A3因余额不足未产生诊断。不得把M2接入普通Run Agent冒充自主生成。充值后可从A2的tested_manifest_2显式继续；所有运行ID、调用参数、tokens、耗时和复现命令见 F:\\AI_EDA\\repair-review-20261102\\summary.json 与 model-calls.csv。原始失败与每轮冻结源码同时保留。", 9.5)
    finish(doc, tables)
    doc.save(TARGET)
    print(TARGET)


if __name__ == "__main__":
    main()
