"""Fill both homework sections from saved stability evidence; preserve old reports."""

import json
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from verification.fill_l3_report import cell_text, rows, widths

BASE = Path(r'D:\大三\人工智能赋能数字电路设计')
SOURCE = BASE / '作业提交要求与报告模板_L3实验记录.docx'
TARGET = BASE / '作业提交要求与报告模板_稳定性复核版.docx'
EVIDENCE = Path(r'F:\AI_EDA\stability-evidence-20261027')
RUNTIME = Path(r'\\wsl.localhost\AI-EDA-Ubuntu-22.04\home\eda\projects\THU_AI_EDA_2026')

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def text(paragraph, value):
    paragraph.text = value
    for run in paragraph.runs:
        run.font.name="微软雅黑"; run.font.size=Pt(10)
        run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"),"微软雅黑")

def number(value, places=2):
    return f"{value:.{places}f}" if value is not None else "N/A"

def main():
    data=read(EVIDENCE/"summary.json")
    aes=data["selected_aes"]; l3=data["selected_l3"]
    report=read(RUNTIME/"runs/agent"/l3["run_id"]/l3["tester"]/"test_report.json")
    old=read(EVIDENCE/"aes-parallel-recheck/test_report.json")["metrics"]
    doc=Document(SOURCE); t=list(doc.tables); p=list(doc.paragraphs)
    text(p[0],"AI EDA 课程作业稳定性与性能复核报告")
    text(p[1],"2026 年 10 月 27 日复核版")
    text(p[2],"本次复核处理 AES 面积功耗偏大和 L3 Agent 重复运行失败两个问题。AES 最终采用双 S-box 资源复用 Prompt，连续三次重新生成均通过教师测试及每组 100 个随机向量。L3 默认复用前次已验证基线并由 Agent 独立提出优化，连续三次完整运行通过。这不代表从零生成已经稳定。旧报告和失败记录保留，以下指标以本次固定工具链重测为准。")
    cell_text(t[0].cell(4,1),"源码、报告及原始记录压缩包\nF:\\AI_EDA\\AI_EDA_稳定性复核与PPA记录_20261027.zip")
    cell_text(t[0].cell(4,3),"Ubuntu 22.04 / Python 3.10.12\nIcarus 11.0 / Yosys 0.70+post\nOpenSTA 3.1.0 / gscl45nm / 10 ns")
    prompt=(BASE/"THU_AI_EDA_2026/content/prompts/homework/H1_custom.md").read_text(encoding="utf-8")
    cell_text(t[1].cell(0,0),prompt,8.5)
    cell_text(t[2].cell(0,0),"先固定接口、同步低有效复位、start && ready 接收、忙时忽略 start、done 单周期，再约束大端字节序和 AES 轮函数。旧 Prompt 自行要求 16 个数据 S-box 加 4 个密钥 S-box，并非教师的最低要求，造成过量复制。\n新版只保留两个共享查表 S-box：KEY_A、KEY_B 各处理两个密钥字节；SUB 阶段每拍替换两个数据字节并循环移位，八拍覆盖 16 字节；ROUND 先 ShiftRows，前九轮再 MixColumns，最后异或本轮密钥。每轮 11 拍，共 110 拍。只保存当前 128 位状态和 128 位轮密钥，密文复用状态寄存器。\n初版双 S-box 连续三次失败，生成代码在前九轮直接对 state 做 MixColumns，漏掉 ShiftRows。Prompt 增加 sr_val=shiftrows(state_reg)、mc_val=mixcolumns(sr_val) 和末轮旁路表达式，固定中间变量位宽与更新顺序后，新三次采样全部通过。",10)
    text(p[18],"只有教师 TB 通过才评估 PPA，选择额外随机验证通过且面积×功耗最小的候选。本轮共 15 次采样，保留其中 5 次失败；最终 Prompt 的新三次采样为 3/3，不宣称全部探索均成功。")
    text(p[21],"P1 参数：deepseek-flash，temperature=0.2，max_tokens=12000，thinking=off，不发送 reasoning_effort。tokens 为 API 总量，输入/输出见 model-calls.csv。时间为生成至首次 TB 结束，另列 PPA 耗时。面积为 gscl45nm 库面积单位，总功耗为 mW；JSON 原始值为 W。")
    labels={x["run_id"]:f"A{i:02}" for i,x in enumerate(data["aes"],1)}
    values=[]
    for x in data["aes"]:
        arch={"2 lookup":"2 查表","4 lookup":"4 查表","4 Boolean":"4 布尔"}[x["architecture"]]
        note="最终采用" if x["run_id"]==aes["run_id"] else ("随机100通过" if x["oracle_passed"] else "见日志")
        values.append([labels[x["run_id"]],"P1\n"+arch,"PASS" if x["passed"] else "FAIL",f'{x["tokens"]:,}',f'{x["duration_ms"]/1000:.2f} s\nPPA {number(x["ppa_ms"]/1000 if x["ppa_ms"] else None)} s',number(x["area"]),number(x["power_w"]*1000 if x["power_w"] else None,4),note])
    rows(t[4],values); widths(t[4],[.45,.75,.5,.65,.95,.9,.85,1.45])
    comparison=(f'同一新工具链下，旧并行 RTL 重测面积 {old["area"]:.4f}、功耗 {old["total_power_w"]*1000:.4f} mW；新方案面积 {aes["area"]:.4f}、功耗 {aes["power_w"]*1000:.4f} mW，分别下降 {(1-aes["area"]/old["area"])*100:.2f}% 和 {(1-aes["power_w"]/old["total_power_w"])*100:.2f}%。不能直接与旧 Yosys 0.9 的结果相减。')
    mapping="\n".join(f'{labels[x["run_id"]]} = {x["run_id"]}' for x in data["aes"])
    cell_text(t[5].cell(0,0),f'最终采用 {labels[aes["run_id"]]}。教师 TB 5 vectors PASS；额外 OpenSSL 参考实现对照 100 组随机输入通过，并检查忙时 start、输入变化与握手，最大延迟 110 拍。\n'+comparison+f'\n布尔 S-box 面积约 1.12 万，但课程口径功耗约 62.5 至 67.2 mW，不因面积小就选它。课程功耗使用映射网表与原 RTL VCD，只有匹配信号得到直接标注，其余活动由工具传播或默认。独立门级 VCD 交叉检查中，最终双 S-box 为 {aes["gate_crosscheck_w"]*1000:.4f} mW；这是另列的零延迟诊断，不替换课程结果。\n助教截图只有“1000，和130吧”，缺少单位、时钟、架构和工具条件，暂不能数值对齐。需确认 1000 是单元数、库面积还是物理面积，130 的功耗单位以及参考实现；本报告不做任意缩放。\n证据根目录 F:\\AI_EDA\\stability-evidence-20261027。原始运行位于 runs/prompt/homework_aes128/<run_id>/，包含 prompt、RTL、metadata、test_report、oracle 和 ppa。\n'+mapping,9)
    fill_agent(doc,t,p,data,l3,report)
    finish(doc,t)
    doc.save(TARGET)
    print(TARGET)

def fill_agent(doc,t,p,data,l3,report):
    flow=EVIDENCE/'ui/agent-flow.png'
    if flow.exists():
        cell_text(t[7].cell(0,0),'复核后的工作台流程',10)
        t[7].cell(0,0).add_paragraph().add_run().add_picture(str(flow),width=Inches(6.05))
    cell_text(t[8].cell(0,0),
        '固定流程为 Parser → Planner → Coder → Tester；需要修复或优化时进入 Diagnoser → Patch Agent → Tester。'
        '默认 verified_incremental：Planner 加载冻结合同，Coder 导入前次人机协同修复所得 '
        'agent-20261027T111341960911 的 tester-04 快照，共 11 个模块。加载时验证源码和合同 SHA-256；'
        '复用事件不伪装成模型生成，也不计入模型调用。Parser、Diagnoser 和 Patch Agent 仍真实调用模型。\n'
        'fresh 保留为实验选项。本次两次恢复旧失败快照仍未通过，不能据此声称独立从零生成成功。'
        '若要求每次从空目录生成整核，本次稳定性证据不足以满足该额外要求。\n'
        'Tester 先运行等待 0/3 拍的短程序冒烟检查，失败时跳过昂贵整套测试与 PPA；通过后运行原教师 TB、'
        '独立 L3 压力测试和 PPA。测试前冻结不可变源码快照，交接只传报告引用、去重失败、首尾 trace、'
        '有限源码窗口和哈希。Diagnoser 输出最小修改范围，Patch Agent 原子应用锚点补丁；连续三次相同故障指纹即停止盲修。'
        '候选从最优已验证快照出发，功能或 PPA 退化则保留原版本。',9.5)
    cell_text(t[9].cell(2,1),'默认加载已验证合同；fresh 模式才调用模型规划')
    cell_text(t[9].cell(2,4),'记录复用来源；不算新规划；PRF40、ROB8、IQ8、LSQ2 不缩容')
    cell_text(t[9].cell(3,1),'默认导入哈希校验的 11 模块基线；fresh 逐模块生成')
    cell_text(t[9].cell(3,4),'默认无生成调用；哈希不符即报错；fresh 仍为实验模式')
    cell_text(t[9].cell(4,4),'无模型调用；冒烟 → 教师 TB → 压力测试 → PPA；启动探针不引用私有层级')
    text(p[28],'S1 至 S3 全局参数为 deepseek-flash、thinking=on/low、max_tokens=24000、max_calls=16、最多修复4轮、优化2轮；'
        '思考开启时不发送 temperature。Diagnoser 实际覆盖为思考关闭、T=0.2、max_tokens=8000，'
        'Patch Agent 为思考关闭、T=0.2、max_tokens=16000。逐调用参数及输入/输出 usage 见 model-calls.csv。'
        '本表保留原失败与两次恢复失败；tokens 为 API 总量，时间为运行全流程，不含本地编码与报告。'
        'S1 至 S3 共享基线，不是三次从零生成。面积为库面积单位，功耗为 mW。')
    labels={}; stable=0; failed=0; values=[]
    for x in data['agents']:
        if x['mode']=='verified_incremental':
            stable+=1; label=f'S{stable}'; note='共享已验证基线；真实优化2轮；3个候选全部通过'
        elif x['run_id']=='agent-20261027T135106050927':
            label='原失败';note='用户重复运行失败；无首次发射/退休；保留原记录'
        else:
            failed+=1;label=f'F{failed}';note='恢复失败快照；启动/退休仍未解决，不评 PPA'
        labels[x['run_id']]=label
        chosen=next((c for c in data['candidates'] if c['run_id']==x['run_id'] and c['selected'] and c['functional_passed']),None)
        values.append([label,'L3','PASS' if x['status']=='passed' else 'FAIL',number(chosen['area'] if chosen else None,1),
            number(chosen['power_w']*1000 if chosen else None,4),f"{x['total_tokens']:,}",f"{x['duration_ms']/1000:.2f} s",note])
    rows(t[10],values);widths(t[10],[.55,.35,.5,.9,.8,.8,.8,1.8])
    values=[]
    for x in data['candidates']:
        if not x['functional_passed']:continue
        outcome='运行内最优' if x['selected'] else '后被替代'
        if x['run_id']==l3['run_id'] and x['tester']==l3['tester']:outcome='最终采用'
        values.append([f"{labels[x['run_id']]} T{int(x['tester'].split('-')[-1])}",'PASS',number(x['area'],1),number(x['power_w']*1000,4),number(x['area']*x['power_w']),outcome])
    rows(t[11],values)
    baseline=next(c for c in data['candidates'] if c['run_id']==l3['run_id'] and c['tester']=='tester-01')
    text(p[30],'三次运行均先测试冻结基线，再测试两个独立提出的优化候选，共 9 个候选通过功能与 PPA。'
        '优化针对 rename_unit 的动态索引空闲表扫描等结构，每次只作小范围修改，不缩小容量、不改变提交语义。乘积使用 W 计算。'
        f"最终采用 {labels[l3['run_id']]} / {l3['tester']}；相对同次基线面积下降 {(1-l3['area']/baseline['area'])*100:.2f}%，"
        f"功耗下降 {(1-l3['power_w']/baseline['power_w'])*100:.2f}%，乘积下降 {(1-l3['area']*l3['power_w']/(baseline['area']*baseline['power_w']))*100:.2f}%。")
    values=[]
    names={'trained_branch64':'热分支训练64','teaching_crc32_64bytes':'CRC32 64字节教学核',
        'teaching_array_sum64':'数组求和64','teaching_branch_predict64':'混合分支64','teaching_ooo_overlap64':'乱序访存重叠64'}
    for x in report['performance']['benchmarks']:
        values.append([names.get(x['benchmark'],x['benchmark']),'PASS',str(x['cycles']),f"{x['cpi']:.4f}",f"{x.get('mispredicts',0)}/{x.get('branches',0)}",str(x.get('ooo_bypass','N/A')),f"退休 {x['instructions']}；IPC={x['ipc']:.3f}"])
    rows(t[12],values);widths(t[12],[1.4,.5,.6,.65,.9,.7,1.75])
    mapping='\n'.join(f"{labels[x['run_id']]} = {x['run_id']}" for x in data['agents'])
    cell_text(t[13].cell(0,0),
        f"最终采用 {l3['run_id']}/{l3['tester']}。教师 TB 通过 21 项测试、302307 次检查，目标=声明=行为验证=L3，features=7；"
        '独立补充压力测试通过 5 项，另作一次独立整核复测，'
        f"面积 {l3['area']:.4f}、功耗 {l3['power_w']*1000:.6f} mW 均复现。Cache、重命名单元独立测试通过。\n"
        'CPU 保留 I/D Cache、BTB/BHT、乱序发射、重命名、ROB 顺序提交和分支恢复；独立 ALU 能越过等待 load，'
        '架构寄存器只显示已提交值。性能表来自所选候选输出。\n'
        '本次修复诊断探针固定引用旧私有信号名的问题，改用公开接口和有限启动波形；发现 issue_valid=0 时无效 ALU 分支结果为 X，'
        '可能污染 redirect/flush。fresh 的 Planner/Diagnoser 增加有效位限定要求，但恢复实验仍失败。可靠复现策略是明确复用已验证基线，'
        '并在功能门槛内优化，不能把失败写成从零成功。\n'
        'Yosys 由官方 v0.70 源码构建，实际版本 0.70+post，commit 0c11ea098be065e585a90a06e7b7da49936cf231；'
        '外部 ABC、OpenSTA 3.1.0、gscl45nm、10 ns。PPA 为前布局无寄生估算，不证明 100 MHz 时序收敛。'
        '教师 Spec/TB 与 Git HEAD 的全部 19 个文件哈希一致，见 teacher_integrity.json。\n'
        '复现入口 verification/README_STABILITY.md；工作台 http://127.0.0.1:8000/。汇总、参数、usage 与候选表见 '
        'F:\\AI_EDA\\stability-evidence-20261027；原始记录在 runs/agent/<run_id>/，含 events、handoffs、快照和 PPA 日志。'
        '23 项 Python 单元测试通过。旧报告单独保留，不把旧工具链数据当作新实验。\n'+mapping,9.5)

def finish(doc,tables):
    # The old fixed break stranded one line of the expanded AES explanation.
    for br in list(doc.paragraphs[16]._p.findall('.//'+qn('w:br'))):
        if br.get(qn('w:type'))=='page':br.getparent().remove(br)
    for name in ('Title','Heading 1','Heading 2'):
        doc.styles[name].font.color.rgb=RGBColor(0,0,0)
        props=doc.styles[name]._element.find(qn('w:pPr'))
        if props is not None:
            for border in list(props.findall(qn('w:pBdr'))):props.remove(border)
    for paragraph in doc.paragraphs[:2]:
        for border in list(paragraph._p.findall('.//'+qn('w:pBdr'))):border.getparent().remove(border)
    for table in tables:
        props=table._tbl.tblPr
        borders=props.find(qn('w:tblBorders'))
        if borders is None:
            borders=OxmlElement('w:tblBorders');props.append(borders)
        for name in ('top','left','bottom','right','insideH','insideV'):
            el=borders.find(qn('w:'+name))
            if el is None:el=OxmlElement('w:'+name);borders.append(el)
            for key,value in [('val','single'),('sz','4'),('color','D9D9D9')]:el.set(qn('w:'+key),value)
        if len(table.rows)>1:
            for cell in table.rows[0].cells:
                props=cell._tc.get_or_add_tcPr();shading=props.find(qn('w:shd'))
                if shading is None:shading=OxmlElement('w:shd');props.append(shading)
                shading.set(qn('w:fill'),'E8EEF3')
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:run.font.color.rgb=RGBColor(0,0,0)

if __name__=='__main__':main()
