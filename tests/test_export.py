# -*- coding: utf-8 -*-
"""导出功能测试（全部 mock API，不发真实请求）

E1 未测评时点导出 → 提示未找到数据
E2 仅 DeepSeek + Qwen 完成 → 导出 zip 成功，摘要标注"不完整"，无 SHAP 分析表
E3 三模型完成后导出 → zip 含 xlsx(10 表) + 分析摘要 + 2 个 JSON；明细 144 行、SHAP/效率性齐全
"""
import atexit
import json
import os
import shutil
import sys
import tempfile
import zipfile

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL_DIR)
import pharme_eval as core  # noqa: E402
import app  # noqa: E402

# ---- 测试隔离：所有写入落到临时目录，严禁触碰真实 outputs ----
TEST_OUT = tempfile.mkdtemp(prefix="pharme_export_test_")
app.OUT_DIR = TEST_OUT
app._STATE_CACHE.clear()
atexit.register(shutil.rmtree, TEST_OUT, True)

NAME = "药学服务"
ELEMENTS = {"P": "pp", "H": "hh", "A": "aa", "R": "rr", "M": "mm", "E": "ee"}
MATERIALS = ""
GOLD_BASIC = "gb1\ngb2"
GOLD_ADV = "ga1"
API_ARGS = ("https://mock/v1", "sk-mock", "judge-mock",
            "ds-mock", "qw-mock", "km-mock",
            "", "", "", "", "", "")

RAW_OK = {"harm_severity": 1, "harm_likelihood": 1,
          "basic_coverage": 3, "advanced_coverage": 3,
          "constraint_understanding": 3, "method_execution": 1,
          "inappropriate_content": 1, "hallucination": 1}


def mock_preflight(api_cfg, include_gen=True, only=None):
    return []


def mock_gen_iter(api_cfg, prompt, model_key, n_valid=core.N_RUNS):
    for k in range(n_valid):
        yield ("answer", f"模拟回答-{model_key}-{k}")


def mock_judge(api_cfg, scenario_data, txt):
    return dict(RAW_OK), False


def mock_cons(api_cfg, three):
    return 3


core.preflight_api = mock_preflight
core.generate_answers_iter = mock_gen_iter
core.judge_answer = mock_judge
core.judge_consistency = mock_cons


def run_model(btn, force=True):
    outs = list(btn(NAME, ELEMENTS["P"], ELEMENTS["H"], ELEMENTS["A"], ELEMENTS["R"],
                    ELEMENTS["M"], ELEMENTS["E"], MATERIALS, GOLD_BASIC, GOLD_ADV,
                    force, *API_ARGS))
    return outs[-1]


def export():
    return app.ui_export_all(NAME, ELEMENTS["P"], ELEMENTS["H"], ELEMENTS["A"], ELEMENTS["R"],
                             ELEMENTS["M"], ELEMENTS["E"], MATERIALS, GOLD_BASIC, GOLD_ADV)


def export_docx():
    return app.ui_export_docx(NAME, ELEMENTS["P"], ELEMENTS["H"], ELEMENTS["A"], ELEMENTS["R"],
                              ELEMENTS["M"], ELEMENTS["E"], MATERIALS, GOLD_BASIC, GOLD_ADV)


# ============ E1：未测评时导出 ============
print("=" * 26, "E1 未测评时导出", "=" * 26)
f, msg = export()
assert f is None and "未找到测评数据" in msg, msg[:200]
print("✅ E1 通过：无数据时给出明确提示")

# ============ E2：仅两模型完成 → 部分导出 ============
print("=" * 26, "E2 部分导出", "=" * 26)
run_model(app.ui_model_run_ds)
run_model(app.ui_model_run_qw, force=False)
f, msg = export()
assert f and os.path.exists(f), msg[:300]
assert "不完整" in msg, msg[:300]
with zipfile.ZipFile(f) as z:
    names = z.namelist()
    assert len(names) == 4, names
    xlsx_name = [n for n in names if n.endswith(".xlsx")][0]
    md_name = [n for n in names if n.endswith(".md")][0]
    assert any("runstate" in n for n in names) and any("场景快照" in n for n in names)
    md = z.read(md_name).decode("utf-8")
    assert "不完整" in md and "Shapley" not in md, md[:400]
from openpyxl import load_workbook  # noqa: E402
wb = load_workbook(os.path.join(TEST_OUT, xlsx_name))
sheets = wb.sheetnames
assert "01_评分明细_单次回答" in sheets and "07_SHAP值与置信区间" not in sheets, sheets
assert wb["01_评分明细_单次回答"].max_row == 96 + 1  # 两模型 96 条 + 表头
assert wb["02_回答原文"].max_row == 96 + 1
assert wb["03_一致性判断"].max_row == 32 + 1
assert wb["09_场景与金标准"].max_row == 12  # 表头 + 6 要素 + 材料/基础/进阶/否决/规则
f4b, msg4b = export_docx()
assert f4b and os.path.exists(f4b), msg4b[:300]
assert "从简" in msg4b, msg4b[:300]
from docx import Document as _Doc  # noqa: E402
_d = _Doc(f4b)
assert any("（数据未完整" in p.text for p in _d.paragraphs), "部分导出应在五/六节标注数据未完整"
print(f"✅ E2 通过：部分导出成功（{os.path.basename(f)}），标注不完整、跳过 SHAP 分析；Word 附件从简生成")

# ============ E3：三模型完成后全量导出 ============
print("=" * 26, "E3 全量导出", "=" * 26)
run_model(app.ui_model_run_km, force=False)
f, msg = export()
assert f and os.path.exists(f), msg[:300]
assert "全部评分明细" in msg and "144 条" in msg and "已跳过" not in msg, msg[:300]
with zipfile.ZipFile(f) as z:
    names = z.namelist()
    assert len(names) == 4, names
    md = z.read([n for n in names if n.endswith(".md")][0]).decode("utf-8")
    assert "Shapley" in md and "效率性校验" in md and "16 组合均分" in md
xlsx = os.path.join(TEST_OUT, [n for n in names if n.endswith(".xlsx")][0])
wb = load_workbook(xlsx)
expected = ["00_导出说明", "01_评分明细_单次回答", "02_回答原文", "03_一致性判断",
            "04_16组合评分", "05_模型层效用", "06_联盟效用", "07_SHAP值与置信区间",
            "08_效率性与数据链条", "09_场景与金标准"]
assert wb.sheetnames == expected, wb.sheetnames
assert wb["01_评分明细_单次回答"].max_row == 144 + 1
assert wb["02_回答原文"].max_row == 144 + 1
assert wb["03_一致性判断"].max_row == 48 + 1
assert wb["04_16组合评分"].max_row == 16 + 1
assert wb["05_模型层效用"].max_row == 48 + 1
assert wb["06_联盟效用"].max_row == 16 + 1
assert wb["07_SHAP值与置信区间"].max_row == 4 + 1
hdr = [c.value for c in wb["01_评分明细_单次回答"][1]]
assert "折算总分" in hdr and "一票否决" in hdr, hdr
assert any("语义相似度" in str(h) for h in hdr), hdr
shap_rows = {r[1].value: r[2].value for r in wb["07_SHAP值与置信区间"].iter_rows(min_row=2)}
assert set(shap_rows) == {"P", "R", "M", "E"}, shap_rows
print(f"✅ E3 通过：全量导出成功（{os.path.basename(f)}），10 表齐全、144 条明细、SHAP 4 要素")

# ============ E4：Word 附件导出（六节框架） ============
print("=" * 26, "E4 Word 附件导出", "=" * 26)
f4, msg4 = export_docx()
assert f4 and os.path.exists(f4), msg4[:300]
assert "六节框架" in msg4 and "H+A" in msg4, msg4[:300]
from docx import Document  # noqa: E402
d = Document(f4)
texts = [p.text.strip() for p in d.paragraphs if p.text.strip()]
for sec in ["一、场景分类", "二、案例内容（PHARM-E 六元素）", "三、金标准",
            "四、模型评分明细", "五、SHAP 值", "六、该场景推荐的元素组合"]:
    assert sec in texts, (sec, texts[:24])
joined = "\n".join(texts)
for frag in ["pp", "gb1", "ga1", "推荐元素组合：H+A", "效率性校验"]:
    assert frag in joined, frag
assert "P（任务目的）" in joined and "E（循证依据与伦理准则）" in joined
assert len(d.tables) == 6, [len(t.rows) for t in d.tables]   # 场景分类1 + 明细2 + SHAP 1 + 推荐2
detail_tbl = max(d.tables, key=lambda t: len(t.rows))
assert len(detail_tbl.rows) == 144 + 1, len(detail_tbl.rows)
hdrs = [c.text for c in detail_tbl.rows[0].cells]
assert "折算总分" in hdrs and "一票否决" in hdrs, hdrs
print(f"✅ E4 通过：Word 附件生成（{os.path.basename(f4)}），六节齐全、6 表、明细 144 行")

print("\n导出功能测试全部通过 ✔")
