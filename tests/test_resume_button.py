# -*- coding: utf-8 -*-
"""断点续跑测试 D：按钮级（Gradio 事件接线 + UI 渲染）

启动 build_ui() 出来的界面（mock API），用 gradio_client 按 run_btn 的真实输入顺序
（scen, p, h, a, r, m, e, materials, gold_basic, gold_adv, force_restart, 12 项 API 配置）提交：
  D1 断点恢复：force_restart=False → 日志出现 ♻️ 断点提示并续跑
  D2 强制重开：force_restart=True  → 日志出现强制重开提示、全部重生成
验证 ① force_restart 参数按钮位接线正确；② 断点文案经 UI 输出渲染。
"""
import atexit
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

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
ELEMENTS = {"P": "p", "H": "h", "A": "a", "R": "r", "M": "m", "E": "e"}
MATERIALS = ""
GOLD_BASIC = "gb1"
GOLD_ADV = "ga1"
API_ARGS = ("https://mock/v1", "sk-mock", "judge-mock",
            "ds-mock", "qw-mock", "km-mock",
            "", "", "", "", "", "")

n_gen = {"n": 0}


def mock_preflight(api_cfg, include_gen=True):
    return []


def mock_gen_iter(api_cfg, prompt, model_key, n_valid=core.N_RUNS):
    n_gen["n"] += 1
    for k in range(n_valid):
        yield ("answer", f"模拟回答-{model_key}-{k}")


def mock_score_iter(scenario_path, answers_path, api_cfg, out_dir, state=None, checkpoint_cb=None):
    yield ("done", {"report": "mock.xlsx",
                    "phi": {"P": 1.0, "R": 0.5, "M": 0.2, "E": 0.1},
                    "eff": {"sum_phi": 1.8, "v_full_minus_empty": 1.8, "diff": 0.0},
                    "ci": None,
                    "chain": {"单次回答效用条数": 144, "模型层效用值个数": 48, "联盟效用值个数": 16}})


core.preflight_api = mock_preflight
core.generate_answers_iter = mock_gen_iter
core.run_score_iter = mock_score_iter

# ---------- 构造 143 条断点 ----------
combos = core.build_combinations(ELEMENTS, MATERIALS)
answers = {}
last_cell = (combos[-1][0], core.MODEL_KEYS[-1])
for cname, _, _ in combos:
    answers[cname] = {mk: [f"存档-{cname}-{mk}-{i}" for i in range(2 if (cname, mk) == last_cell else 3)]
                      for mk in core.MODEL_KEYS}
fp = hashlib.md5((json.dumps(ELEMENTS, sort_keys=True, ensure_ascii=False)
                  + "||" + MATERIALS).encode("utf-8")).hexdigest()[:12]
rs_path = os.path.join(app.OUT_DIR, f"{NAME}_runstate.json")
json.dump({"fingerprint": fp, "status": "generating", "answers": answers,
           "judge_raw": {}, "consistency": {}},
          open(rs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# ---------- 启动界面 + gradio_client 按按钮输入顺序提交 ----------
from gradio_client import Client  # noqa: E402

PORT = 7862
ui = app.build_ui()
ui.launch(server_name="127.0.0.1", server_port=PORT, prevent_thread_lock=True, quiet=True)
try:
    client = Client(f"http://127.0.0.1:{PORT}")
    inputs = [NAME, "p", "h", "a", "r", "m", "e", MATERIALS, GOLD_BASIC, GOLD_ADV, False] + list(API_ARGS)

    # ---- D1：断点恢复（force_restart=False）----
    n_gen["n"] = 0
    job = client.submit(*inputs, api_name="/ui_full_run")
    final = ""
    for out in job:
        final = out or final
    assert "♻️ 检测到断点" in final, "UI 日志未渲染断点提示"
    assert "已生成 143 条回答" in final
    assert final.count("断点跳过（已完成 3/3）") == 47
    assert "（续生成 1 次）" in final
    assert "✅ 生成完成（144/144）" in final and "✅ 评测完成" in final
    assert n_gen["n"] == 1
    print("✅ D1 通过：按钮级断点恢复，♻️ 提示经 UI 渲染，47 跳过 + 续 1 条")

    # ---- D2：强制重新开始（force_restart=True）----
    json.dump({"fingerprint": fp, "status": "generating", "answers": answers,
               "judge_raw": {}, "consistency": {}},
              open(rs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_gen["n"] = 0
    inputs2 = list(inputs); inputs2[10] = True
    job = client.submit(*inputs2, api_name="/ui_full_run")
    final = ""
    for out in job:
        final = out or final
    assert "已勾选强制重新开始" in final
    assert "♻️ 检测到断点" not in final
    assert n_gen["n"] == 48, f"强制重开应 48 单元全重生成，实际 {n_gen['n']}"
    assert "✅ 生成完成（144/144）" in final
    print("✅ D2 通过：按钮级强制重新开始，48 单元全部重生成")
    print("\n按钮级断点续跑测试全部通过 ✔")
finally:
    ui.close()
