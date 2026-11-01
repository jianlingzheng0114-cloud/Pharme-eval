# -*- coding: utf-8 -*-
"""生成 ③ 纯计算模式的数据模板（xlsx）：
1. 纯计算模板_3次重复格式.xlsx —— 空白待填（推荐，可算 bootstrap 95%CI）
2. 纯计算模板_均分格式.xlsx   —— 空白待填（仅均分，CI 不适用）
3. 纯计算填写示例.xlsx        —— 3 次重复格式的示例数值（可直接上传试算）
"""
import os
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与 build_combinations 完全一致的 16 个组合名（H+A 固定 + P/R/M/E 任意子集）
COMBOS = ["H+A",
          "H+A+P", "H+A+R", "H+A+M", "H+A+E",
          "H+A+P+R", "H+A+P+M", "H+A+P+E", "H+A+R+M", "H+A+R+E", "H+A+M+E",
          "H+A+P+R+M", "H+A+P+R+E", "H+A+P+M+E", "H+A+R+M+E",
          "H+A+P+R+M+E"]
RUNS_HEADER = (["提示词组合"]
               + [f"DeepSeek_{i}" for i in (1, 2, 3)]
               + [f"Qwen_{i}" for i in (1, 2, 3)]
               + [f"Kimi_{i}" for i in (1, 2, 3)])
MEAN_HEADER = ["提示词组合", "DeepSeek", "Qwen", "Kimi"]

HDR_FILL = PatternFill("solid", fgColor="D9E2F3")
THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _style(ws, n_cols, n_rows):
    for j in range(1, n_cols + 1):
        c = ws.cell(row=1, column=j)
        c.font = Font(name="黑体", size=11, bold=True)
        c.fill = HDR_FILL
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = BORDER
    for i in range(2, n_rows + 1):
        for j in range(1, n_cols + 1):
            c = ws.cell(row=i, column=j)
            c.font = Font(name="仿宋_GB2312", size=11)
            c.border = BORDER
            if j == 1:
                c.alignment = Alignment(horizontal="left")
            else:
                c.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"


def make_runs_template(path, fill_example=False):
    wb = Workbook()
    ws = wb.active
    ws.title = "3次重复格式"
    ws.append(RUNS_HEADER)
    eff = {"P": -6.0, "R": 2.0, "M": 5.0, "E": 4.0}
    model_off = {"DeepSeek": 0.0, "Qwen": -1.5, "Kimi": 1.0}
    for combo in COMBOS:
        row = [combo]
        if fill_example:
            base = 72.0 + sum(eff[e] for e in eff if e in combo.split("+"))
            for m in ("DeepSeek", "Qwen", "Kimi"):
                for r in range(3):
                    jitter = ((hash((combo, m, r)) % 7) - 3) * 0.7  # ±2.1 抖动
                    row.append(round(base + model_off[m] + jitter, 1))
        else:
            row += [None] * 9
        ws.append(row)
    _style(ws, 10, len(COMBOS) + 1)
    for j in range(2, 11):
        ws.column_dimensions[chr(64 + j)].width = 11
    ws.column_dimensions["A"].width = 18
    wb.save(path)


def make_mean_template(path):
    wb = Workbook()
    ws = wb.active
    ws.title = "均分格式"
    ws.append(MEAN_HEADER)
    for combo in COMBOS:
        ws.append([combo, None, None, None])
    _style(ws, 4, len(COMBOS) + 1)
    ws.column_dimensions["A"].width = 18
    for col in ("B", "C", "D"):
        ws.column_dimensions[col].width = 12
    wb.save(path)



def make_readme_sheet(path):
    """独立说明文件（xlsx 附一个说明工作表的方式不适用——解析器只读第一个工作表）"""
    wb = Workbook()
    ws = wb.active
    ws.title = "填写说明"
    rows = [
        ["③ 纯计算模式数据填写说明", ""],
        ["格式二选一", "A. 3 次重复格式（推荐）：DeepSeek_1..3 / Qwen_1..3 / Kimi_1..3 共 9 个数值列，可算 bootstrap 95%CI"],
        ["", "B. 均分格式：DeepSeek / Qwen / Kimi 共 3 个数值列（3 次折算总分的均值），CI 不适用"],
        ["数值口径", "每个数值 = 对应回答的折算总分（0–100），与导出 xlsx『评分明细_单次回答』的『折算总分』同口径"],
        ["一票否决", "被一票否决的回答按 0 分计入"],
        ["行", "必须 16 行组合齐全：H+A 与 P/R/M/E 任意子集的拼接（工具预置的组合名直接沿用即可）"],
        ["列名", "数值列表头必须严格为 DeepSeek_1..3 / Qwen_1..3 / Kimi_1..3 或 DeepSeek / Qwen / Kimi（区分大小写）"],
        ["空值", "所有数值必须填满，留空会报错（报错会指出是哪个组合哪一列）"],
        ["不要混用", "两种格式不要混在一张表里（出现 _1.._3 列即按 3 次重复格式解析）"],
        ["文件格式", "xlsx 或 csv（UTF-8，逗号分隔）均可；xlsx 只读取第一个工作表"],
        ["上传位置", "工具 ③ 纯计算（无需 API）页 → 上传评分表 → 填结果名称 → ▶ 计算 Shapley 并生成报告"],
    ]
    for r in rows:
        ws.append(r)
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 110
    ws.cell(row=1, column=1).font = Font(name="黑体", size=13, bold=True)
    for i in range(2, len(rows) + 1):
        ws.cell(row=i, column=1).font = Font(name="黑体", size=11)
        ws.cell(row=i, column=2).font = Font(name="仿宋_GB2312", size=11)
        ws.cell(row=i, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


if __name__ == "__main__":
    p1 = os.path.join(TOOL_DIR, "纯计算模板_3次重复格式.xlsx")
    p2 = os.path.join(TOOL_DIR, "纯计算模板_均分格式.xlsx")
    p3 = os.path.join(TOOL_DIR, "纯计算填写示例.xlsx")
    p4 = os.path.join(TOOL_DIR, "纯计算模板_填写说明.xlsx")
    make_runs_template(p1, fill_example=False)
    make_mean_template(p2)
    make_runs_template(p3, fill_example=True)   # 示例表为合成演示数据（固定种子效应）
    make_readme_sheet(p4)
    for p in (p1, p2, p3, p4):
        print("生成:", p)
