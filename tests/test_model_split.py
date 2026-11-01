# -*- coding: utf-8 -*-
"""分模型测评三按钮 + 合成按钮 测试（全部 mock API，不发真实请求）

M1 无测评数据时点合成 → 提示先跑三按钮
M2 三按钮依次测评 DeepSeek/Qwen/Kimi → 各自 48 生成 + 48 单条 + 16 一致性，逐条落盘
M3 只完成 2 个模型时点合成 → 报"尚未全部完成"并列出缺口
M4 三模型完成后点合成 → 汇总评分明细 + Shapley（链条 144/48/16，xlsx 报告生成）
"""
import atexit
import json
import os
import shutil
import sys
import tempfile

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL_DIR)
import pharme_eval as core  # noqa: E402
import app  # noqa: E402

# ---- 测试隔离：所有写入落到临时目录，严禁触碰真实 outputs（曾误覆盖真实断点）----
TEST_OUT = tempfile.mkdtemp(prefix="pharme_test_")
app.OUT_DIR = TEST_OUT
app._STATE_CACHE.clear()
atexit.register(shutil.rmtree, TEST_OUT, True)

NAME = "药学服务"
ELEMENTS = {"P": "pp", "H": "hh", "A": "aa", "R": "rr", "M": "mm", "E": "ee"}
MATERIALS = ""
GOLD_BASIC = "gb1"
GOLD_ADV = "ga1"
API_ARGS = ("https://mock/v1", "sk-mock", "judge-mock",
            "ds-mock", "qw-mock", "km-mock",
            "", "", "", "", "", "")

n_gen, n_single, n_cons = {"n": 0}, {"n": 0}, {"n": 0}

RAW_OK = {"harm_severity": 1, "harm_likelihood": 1,
          "basic_coverage": 3, "advanced_coverage": 3,
          "constraint_understanding": 3, "method_execution": 1,
          "inappropriate_content": 1, "hallucination": 1}


def mock_preflight(api_cfg, include_gen=True, only=None):
    return []


def mock_gen_iter(api_cfg, prompt, model_key, n_valid=core.N_RUNS):
    n_gen["n"] += 1
    for k in range(n_valid):
        yield ("answer", f"模拟回答-{model_key}-{k}")


def mock_judge(api_cfg, scenario_data, txt):
    n_single["n"] += 1
    return dict(RAW_OK), False


def mock_cons(api_cfg, three):
    n_cons["n"] += 1
    return 3


core.preflight_api = mock_preflight
core.generate_answers_iter = mock_gen_iter
core.judge_answer = mock_judge
core.judge_consistency = mock_cons


def run_model(btn, force=True):
    outs = list(btn(NAME, ELEMENTS["P"], ELEMENTS["H"], ELEMENTS["A"], ELEMENTS["R"],
                    ELEMENTS["M"], ELEMENTS["E"], MATERIALS, GOLD_BASIC, GOLD_ADV,
                    force, *API_ARGS))
    return outs[-1]  # 日志为累积式，取最终版统计


def merge():
    return app.ui_merge_run(NAME, ELEMENTS["P"], ELEMENTS["H"], ELEMENTS["A"], ELEMENTS["R"],
                            ELEMENTS["M"], ELEMENTS["E"], MATERIALS, GOLD_BASIC, GOLD_ADV)


# ============ M1：无数据点合成 ============
print("=" * 26, "M1 无测评数据点合成", "=" * 26)
out = merge()
assert "未找到测评数据" in out or "尚未全部完成" in out, out[:200]
print("✅ M1 通过：未测评时点合成给出明确提示")

# ============ M2：先测评 DeepSeek + Qwen ============
print("=" * 26, "M2 分模型测评", "=" * 26)
out_ds = run_model(app.ui_model_run_ds)
assert "开始测评 DeepSeek-V3" in out_ds and "✅ 生成完成（48/48）" in out_ds
assert out_ds.count("✓ 第 3/3 条已存档") == 16, "DeepSeek 应生成 16 单元 × 3 条"
assert "✅ DeepSeek-V3 测评完成并已落盘" in out_ds
out_qw = run_model(app.ui_model_run_qw, force=False)
assert "✅ Qwen3-32B 测评完成并已落盘" in out_qw

rs = json.load(open(os.path.join(app.OUT_DIR, f"{NAME}_runstate.json"), encoding="utf-8"))
assert len(rs["judge_raw"]) == 96 and len(rs["consistency"]) == 32, \
    f"两模型应已评 96 单条 + 32 一致性，实际 {len(rs['judge_raw'])}/{len(rs['consistency'])}"
print("✅ M2 通过：DeepSeek、Qwen 各 48 生成 + 48 单条 + 16 一致性，落盘正常")

# ============ M3：缺 Kimi 时点合成 ============
print("=" * 26, "M3 未齐点合成", "=" * 26)
out = merge()
assert "尚未全部完成" in out and "Kimi-K2" in out, out[:300]
print("✅ M3 通过：三模型未齐时拒绝合成并列出缺口")

# ============ M4：完成 Kimi 后合成 ============
print("=" * 26, "M4 合成最终结果", "=" * 26)
out_km = run_model(app.ui_model_run_km, force=False)
assert "✅ Kimi-K2 测评完成并已落盘" in out_km
out = merge()
assert "✅ 合成完成" in out
assert "单次回答效用 144 条" in out and "模型层效用 48 个" in out and "联盟效用 16 个" in out
assert "SHAP值" in out or "SHAP" in out
assert "效率性校验" in out and "报告已生成" in out
assert "_评分明细与SHAP.xlsx" in out
xlsx = os.path.join(app.OUT_DIR, f"{NAME}_评分明细与SHAP.xlsx")
assert os.path.exists(xlsx), "汇总评分明细与 SHAP 报告未生成"
rs = json.load(open(os.path.join(app.OUT_DIR, f"{NAME}_runstate.json"), encoding="utf-8"))
assert rs["status"] == "done"
assert n_gen["n"] == 48 and n_single["n"] == 144 and n_cons["n"] == 48, \
    f"调用计数不对：{n_gen['n']}/{n_single['n']}/{n_cons['n']}"
print("✅ M4 通过：合成输出汇总评分明细 + Shapley（144/48/16），报告已生成")

print("\n分模型测评 + 合成 测试全部通过 ✔")
