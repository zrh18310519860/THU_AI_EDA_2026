"""Fill only Homework 2 slots in the existing course report; preserve AES work."""
import json
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

BASE=Path(r'D:\大三\人工智能赋能数字电路设计')
EVIDENCE=Path(r'F:\AI_EDA\L3-evidence-20261027')
SOURCE=BASE/'作业提交要求与报告模板_AES128补跑记录.docx'
TARGET=BASE/'作业提交要求与报告模板_L3实验记录.docx'

def cell_text(cell,text,size=9):
    cell.text=str(text)
    cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for p in cell.paragraphs:
        p.paragraph_format.keep_with_next=False
        p.paragraph_format.space_before=Pt(3)
        p.paragraph_format.space_after=Pt(3)
        p.paragraph_format.line_spacing=1.08
        for run in p.runs:
            run.font.name='微软雅黑';run.font.size=Pt(size);run.font.color.rgb=RGBColor(0,0,0)
            run._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'微软雅黑')

def widths(table,values):
    table.autofit=False;table.alignment=WD_TABLE_ALIGNMENT.CENTER
    for col,value in zip(table.columns,values):col.width=Inches(value)
    for row in table.rows:
        for cell,value in zip(row.cells,values):cell.width=Inches(value)

def rows(table,values,size=9):
    template=deepcopy(table.rows[1]._tr) if len(table.rows)>1 else None
    for row in list(table.rows)[1:]:table._tbl.remove(row._tr)
    for value in values:
        if template is not None:table._tbl.append(deepcopy(template))
        else:table.add_row()
        for cell,text in zip(table.rows[-1].cells,value):cell_text(cell,text,size)
    header=table.rows[0]._tr.get_or_add_trPr()
    repeat=OxmlElement('w:tblHeader');header.append(repeat)
    for cell in table.rows[0].cells:
        for p in cell.paragraphs:p.paragraph_format.keep_with_next=True
    for row in table.rows:
        trpr=row._tr.get_or_add_trPr()
        for height in list(trpr.findall(qn('w:trHeight'))):trpr.remove(height)
        trpr.append(OxmlElement('w:cantSplit'))

def paragraph_after(doc,element,text,style=None):
    p=OxmlElement('w:p');element.addnext(p)
    from docx.text.paragraph import Paragraph
    paragraph=Paragraph(p,doc._body)
    if style:paragraph.style=style
    paragraph.add_run(text)
    paragraph.paragraph_format.space_after=Pt(6)
    if not style:
        for run in paragraph.runs:
            run.font.name='微软雅黑';run.font.size=Pt(9.5)
            run._element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'微软雅黑')
    return paragraph

def main():
    data=json.loads((EVIDENCE/'l3-summary.json').read_text(encoding='utf-8'))
    report=data['report'];metrics=report['metrics'];run_id=data['selected_run']
    doc=Document(SOURCE);tables=list(doc.tables)
    labels={r['run_id']:f'R{i+1}' for i,r in enumerate(data['records'])}
    # The AES content is not edited; only submission metadata now points at the combined archive.
    cell_text(tables[0].cell(4,1),'源码、报告及原始运行记录压缩包\nF:\\AI_EDA\\AI_EDA_L3智能体与实验记录_20261027.zip')
    rows(tables[6],[
        ['L0 基础五级流水线','未选','N/A','N/A'],['L1 I D Cache','未选','N/A','N/A'],
        ['L2 Cache 加分支预测','未选','N/A','N/A'],
        ['L3 Cache 预测加乱序','已选','rv32i5_ooo','PASS\n目标=声明=行为验证=L3']])
    cell_text(tables[7].cell(0,0),'工作台实际 Agent 流程图',10)
    tables[7].cell(0,0).add_paragraph().add_run().add_picture(str(EVIDENCE/'ui/agent-flow.png'),width=Inches(6.1))
    for edge in ('top','bottom'):
        tables[7].cell(0,0)._tc.tcPr.find(qn('w:tcMar')).find(qn('w:'+edge)).set(qn('w:w'),'100')
    cell_text(tables[8].cell(0,0),
        'Parser → Planner → Coder → Tester；失败后 Tester → Diagnoser → Patch Agent → Tester。图中的 Task Spec 是固定输入，不计入六个子 Agent。图只展示固定执行流水线，不是任意图调度器。\n'
        'Tester 在每次测试前保存不可变 RTL 快照与 SHA-256。完整日志留在 artifact 中，交接只传引用、去重失败组、故障附近 trace 和有限的逐指令比对摘要。Diagnoser 读取相关模块窗口，输出根因、修改范围、需保持的不变量和验证项。Patch Agent 仅接收获准文件及精确锚点，不累积全工程对话。\n'
        '通过教师 TB、独立压力测试及有效 PPA 后再优化。候选从最优快照出发，失败或面积×功耗变差则回退。',9.5)
    agent_rows=[
        ['Parser','判断规格是否有阻塞缺口，记录非阻塞假设','教师 L3 Spec','effective_spec、parser_result','不改教师要求；内部实现选择不询问用户；思考开启 low'],
        ['Planner','冻结模块所有权、端口方向位宽、控制位和并发协议','有效 Spec、分级合同','plan.json、冻结接口和 notes','ROB8/IQ8/PRF40/LSQ2；LSU 完成优先；保留独立 ALU 越过等待 load'],
        ['Coder','逐模块生成；局部语法与合同检查；顶层最后连接','本模块规格、Plan 和接口','各 RTL、不可变 source manifest','L3 思考 low、max_tokens=60000；Icarus 和 Yosys 前端检查；同步复位'],
        ['Tester','固定教师 TB、补充测试、功耗活动、综合和功耗工具','冻结源码快照、测试入口','test_report、failure packet、PPA 日志/VCD','无模型调用；功能失败不评 PPA；测试配置错误不交 RTL 盲修'],
        ['Diagnoser','证据归因与最小修改范围；识别综合热点','压缩 packet、局部源码、近3轮根因','repair brief、文件 SHA-256','T=0.2、思考关闭、max_tokens=8000；优化时一文件一变量'],
        ['Patch Agent','验证源码哈希；原子应用精确锚点补丁','brief、获准文件窗口、协议约束','patch、patch_manifest、新源码快照','T=0.2、思考关闭、max_tokens=16000；拒绝过期、越界、无操作补丁'],
        ['候选选择器','检查有效面积和功耗，保留最优可复现版本','每轮报告及不可变 manifest','candidate_selection.json','功能、验证等级与 PPA 都有效；目标 min(area×total_power)；记录被拒候选']]
    rows(tables[9],agent_rows);widths(tables[9],[.85,1.25,1.25,1.25,1.9])
    notes=[
        '首次生成：端口/复位等问题；未形成有效 L3/PPA。',
        '重新生成11模块；DFT 跳转后停顿，盲修复未解决。',
        '加入逐退休比对定位丢响应；教师 L3 通过。补充程序超长为测试配置错误，主动停止。',
        '修正补充测试后全功能通过；综合开销异常，主动终止并保留日志。',
        '打包检查点使综合完成；3个优化候选中2个功能失败、1个面积略增；保留基线。',
        '单变量优化空闲表、检查点复位及 PRF 写入；数值见候选表。']
    summary_rows=[]
    for i,r in enumerate(data['records']):
        valid=[c for c in data['candidates'] if c['run_id']==r['run_id'] and c['selected_in_run'] and c['ppa_status']=='ok']
        m=valid[0] if valid else {}
        status={'passed':'PASS','failed':'FAIL','cancelled':'停止'}.get(r['status'],r['status'])
        duration=f"{r['duration_ms']/1000:.2f} s" if r.get('duration_ms') else '未记录'
        if r.get('duration_uncertain'):
            duration='未准确记录\n外部中断'
        summary_rows.append([labels[r['run_id']],'L3',status,f"{m['area']:.1f}" if m else 'N/A',
            f"{m['total_power_w']*1000:.3f}" if m else 'N/A',f"{r['total_tokens']:,}" if r.get('total_tokens') is not None else '缺失',
            duration,notes[i] if i<len(notes) else '见原始记录'])
    summary_rows.append(['最终采用','L3','PASS',f"{metrics['area']:.1f}",f"{metrics['total_power_w']*1000:.3f}",
        '包含在上列','包含在上列',f"{labels[run_id]} / {data['selected_tester']}；有效面积×功耗最小。"])
    cell_text(tables[10].cell(0,1),'等级')
    for run in tables[10].cell(0,1).paragraphs[0].runs:run.font.color.rgb=RGBColor(255,255,255)
    rows(tables[10],summary_rows);widths(tables[10],[.45,.35,.5,.9,.7,.85,.8,1.95])
    text=(f"模型均为 deepseek-flash。表内 tokens 为 API total_tokens，不是仅输出量；输入/输出分项及逐调用实际参数见 l3-model-calls.csv。"
        f"累计 {data['total_tokens_known']:,} tokens，{data['total_model_calls']} 次调用。时间为每个 Agent 运行全流程，包含工具；R3 外部中断，准确端到端耗时未保存，不参与时间比较，已完成调用的 usage 仍完整；其余为单调时钟实测，不含本地排查与编写报告。"
        "全局 T=0.2、max_tokens=32000、thinking=on/low、max_calls=80、最多修复10轮；各子 Agent 的覆盖参数见上表，思考开启时不发送 temperature。早期试验的参数差异原样保留在 events.json。面积为 gscl45nm 库面积单位，总功耗单位 mW。")
    p=paragraph_after(doc,tables[10]._tbl,text)
    heading=paragraph_after(doc,p._p,'优化候选逐次记录','Heading 2')
    heading.paragraph_format.keep_with_next=True
    heading.paragraph_format.page_break_before=True
    candidate_table=doc.add_table(rows=1,cols=6)
    candidate_table.style=tables[10].style
    heading._p.addnext(candidate_table._tbl)
    for c,t in zip(candidate_table.rows[0].cells,['候选','功能','面积','总功耗 mW','面积×功耗','选择结果']):cell_text(c,t)
    candidate_rows=[]
    for c in data['candidates']:
        if labels[c['run_id']] not in ('R5','R6'):continue
        valid=c['ppa_status']=='ok'
        final=c['run_id']==run_id and c['tester']==data['selected_tester']
        verdict='最终采用' if final else ('该运行内部最优' if c['selected_in_run'] else '拒绝或未选')
        if c['run_id']==run_id:
            verdict={'tester-01':'对照基线','tester-02':'阶段最优，后被替代','tester-03':'乘积变差，拒绝','tester-04':'最终采用'}[c['tester']]
        candidate_rows.append([labels[c['run_id']]+' '+c['tester'].replace('tester-','T'),
            'PASS' if c['functional_passed'] else 'FAIL',f"{c['area']:.1f}" if valid else 'N/A',
            f"{c['total_power_w']*1000:.3f}" if valid else 'N/A',
            f"{c['area']*c['total_power_w']:.2f}" if valid else 'N/A',verdict])
    rows(candidate_table,candidate_rows);widths(candidate_table,[.8,.5,1,1,1.1,2.1])
    paragraph_after(doc,candidate_table._tbl,
        'R6 T2 将空闲物理寄存器分配改为固定索引轮转，降低动态索引逻辑开销；T3 去掉检查点有效载荷复位，面积下降但功耗上升，乘积变差，拒绝；T4 从 T2 最优快照出发，将 PRF 写入改为每项固定索引的 generate 写入，进一步降面积且保持功耗。每个候选都重新通过功能门槛后才评估 PPA。乘积单位为库面积单位×W。')
    names={'trained_branch64':'热分支训练64','teaching_crc32_64bytes':'CRC32 64字节教学内核',
        'teaching_array_sum64':'数组求和64','teaching_branch_predict64':'混合分支64','teaching_ooo_overlap64':'乱序访存重叠64'}
    benchmark_rows=[]
    for b in report['performance']['benchmarks']:
        branch=f"{b.get('mispredicts',0)}/{b.get('branches',0)}"
        details=f"退休 {b['instructions']}；IPC={b['ipc']:.3f}"
        if 'dmem_reads' in b:details+=f"；D读/写={b['dmem_reads']}/{b['dmem_writes']}"
        benchmark_rows.append([names.get(b['benchmark'],b['benchmark']),'PASS',b['cycles'],f"{b['cpi']:.4f}",branch,
            b.get('ooo_bypass','N/A'),details])
    rows(tables[11],benchmark_rows);widths(tables[11],[1.5,.55,.65,.7,.85,.65,1.6])
    same_run=[c for c in data['candidates'] if c['run_id']==run_id and c['ppa_status']=='ok']
    baseline=same_run[0]
    area_gain=(1-metrics['area']/baseline['area'])*100
    power_gain=(1-metrics['total_power_w']/baseline['total_power_w'])*100
    product_gain=(1-metrics['area']*metrics['total_power_w']/(baseline['area']*baseline['total_power_w']))*100
    cell_text(tables[12].cell(0,0),
        f"最终采用 {run_id}/{data['selected_tester']}，目标、声明与行为验证均为 L3。教师 TB 通过21项测试、302307次检查，独立补充测试通过5项，并对所选快照另作一次独立整核复测。相对同一轮基线，面积降低 {area_gain:.2f}%，总功耗降低 {power_gain:.2f}%，面积×功耗降低 {product_gain:.2f}%；未改善的候选均保留记录而不替换基线。\n"
        '结构检查：8项 ROB、8项 IQ、40个物理寄存器、2项 LSU 队列；I/D Cache 各256 B、16行、每行16 B；16项 BTB 和2-bit BHT。ALU 发射与较老 load 等待确实重叠，架构寄存器只显示已提交值。黑盒主 TB 不是任意程序正确性的证明，因此另测 WAR/队列压力、分支反复恢复、错误路径 store/ECALL、3组160条随机指令，并独立验证 Cache 和重命名模块。\n'
        '主要瓶颈是前端串行请求/响应节奏、阻塞式 D miss 和 RAT/PRF 选择逻辑，表中 CPI 仍大于1。乱序重叠并不表示所有工作负载都提速；本实验没有生成可比较的低等级 CPU，不作跨等级加速比结论。benchmark 为课程教学内核，不是官方 CoreMark。\n'
        '重构过程含人工介入：修正 Planner 端口合同；补充逐退休 ISA 比对，定位跳转时 I-cache 丢弃旧响应而前端仍等待的死锁；修正自编补充测试超出192条汇编容量的问题；将综合日志中73472个检查点写 mux 的热点交给诊断器。随后由 Diagnoser/Patch Agent 产生有哈希和精确锚点的补丁，Tester 实际运行门槛验证。不能把这些过程描述为未经指导的一次性全自动成功。\n'
        'PPA 方法：Yosys 0.9 + gscl45nm，OpenSTA 3.1.0，10 ns 时钟；固定800周期教师功耗程序产生 RTL VCD。无布局、寄生、CTS 或时序收敛证明；只有匹配的映射引脚获得 VCD 活动，其余活动由传播/默认值补全，故属于前布局估算，不能当签核功耗。功耗按工具舍入原值报告，微小差异应谨慎解释。\n'
        '证据目录 F:\\AI_EDA\\L3-evidence-20261027：l3-summary.json、逐运行/候选/调用/benchmark CSV、UI截图与教师文件前后哈希。原始服务器记录在 /home/eda/projects/THU_AI_EDA_2026/runs/agent/<run_id>/，包括全部Prompt、推理、patch、handoff、不可变RTL、VCD及Yosys/OpenSTA脚本和日志。\n'
        '复现：见压缩包内 verification/README_L3.md。只验收最终 RTL 可运行 verification.verify_selected_l3；重新付费生成可运行 verification.run_l3。API Key 不写入报告或归档。',9.5)
    for p in doc.paragraphs:
        if p.text.startswith(('截图下方说明：','请特别说明：','低等级项目不适用的指标')):p.text=''
        if p.text=='性能结论与证据位置':p.paragraph_format.page_break_before=True
    in_l3=False
    for p in list(doc.paragraphs):
        if p.text.startswith('作业二 EDA Agent'):in_l3=True
        if in_l3 and not p.text.strip() and not p._p.xpath('.//w:drawing'):
            p._element.getparent().remove(p._element)
    # Match the retained template while making added L3 tables fit the printable width.
    for table in tables[6:]+[candidate_table]:
        borders=table._tbl.tblPr.find(qn('w:tblBorders'))
        if borders is None:borders=OxmlElement('w:tblBorders');table._tbl.tblPr.append(borders)
        for name in ('top','left','bottom','right','insideH','insideV'):
            element=OxmlElement('w:'+name);element.set(qn('w:val'),'single');element.set(qn('w:sz'),'4');element.set(qn('w:color'),'D9D9D9');borders.append(element)
    doc.save(TARGET)
    verify=Document(TARGET)
    assert 'L3' in ''.join(c.text for t in verify.tables for row in t.rows for c in row.cells)
    for old,new in zip(Document(SOURCE).tables[1:6],verify.tables[1:6]):
        assert [[c.text for c in row.cells] for row in old.rows]==[[c.text for c in row.cells] for row in new.rows]
    print(TARGET)

if __name__=='__main__':main()
