# -*- coding: utf-8 -*-
"""断点续跑测试 B/C：生成阶段恢复 + 强制重新开始（全部 mock API，不发真实请求）

测试 B：构造 47/48 条件单元已完成（最后单元 Kimi 仅 2 条）的 runstate，
        调 ui_full_run 验证：检测断点 → 47 单元跳过 → 续生成 1 条 → 144/144 → status=done
测试 C：同一 runstate + force_restart=True，验证忽略断点、48 单元全部重生成
"""
import atexit
import hashlib
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

OUT_DIR = app.OUT_DIR
NAME = "药学服务"
ELEMENTS = {"P": "p", "H": "h", "A": "a", "R": "r", "M": "m", "E": "e"}
MATERIALS = ""
GOLD_BASIC = "gb1"
GOLD_ADV = "ga1"

# ---------- mock（替换 API 调用，统计次数） ----------
n_gen = {"calls": [], "n": 0}
n_score = {"n": 0}


def mock_preflight(api_cfg, include_gen=True):
    return []


def mock_gen_iter(api_cfg, prompt, model_key, n_valid=core.N_RUNS):
    n_gen["calls"].append((model_key, n_valid))
    n_gen["n"] += 1
    for k in range(n_valid):
        yield ("progress", f"模拟生成第 {k + 1}/{n_valid} 条")
        yield ("answer", f"模拟回答-{model_key}-{k}")


def mock_score_iter(scenario_path, answers_path, api_cfg, out_dir, state=None, checkpoint_cb=None):
    n_score["n"] += 1
    yield ("progress", "单条评分 1/144（模拟）")
    yield ("done", {"report": "mock.xlsx",
                    "phi": {"P": 1.0, "R": 0.5, "M": 0.2, "E": 0.1},
                    "eff": {"sum_phi": 1.8, "v_full_minus_empty": 1.8, "diff": 0.0},
                    "ci": None,
                    "chain": {"单次回答效用条数": 144, "模型层效用值个数": 48, "联盟效用值个数": 16}})


core.preflight_api = mock_preflight
core.generate_answers_iter = mock_gen_iter
core.run_score_iter = mock_score_iter

# ---------- 构造断点 runstate：47 单元×3 条 + 1 单元×2 条 = 143 条 ----------
combos = core.build_combinations(ELEMENTS, MATERIALS)
assert len(combos) == 16, f"组合数应为 16，实际 {len(combos)}"
answers = {}
last_cell = (combos[-1][0], core.MODEL_KEYS[-1])   # 最后组合 × Kimi
for cname, _, _ in combos:
    answers[cname] = {}
    for mk in core.MODEL_KEYS:
        n = 2 if (cname, mk) == last_cell else 3
        answers[cname][mk] = [f"存档回答-{cname}-{mk}-{i}" for i in range(n)]
n_ans = sum(len(t) for bm in answers.values() for t in bm.values())
assert n_ans == 143, f"断点回答数应为 143（47×3+2），实际 {n_ans}"

fp = hashlib.md5((json.dumps(ELEMENTS, sort_keys=True, ensure_ascii=False)
                  + "||" + MATERIALS).encode("utf-8")).hexdigest()[:12]
rs_path = os.path.join(OUT_DIR, f"{NAME}_runstate.json")
state = {"fingerprint": fp, "status": "generating", "answers": answers,
         "judge_raw": {}, "consistency": {}}
json.dump(state, open(rs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

API_ARGS = ("https://mock/v1", "sk-mock", "judge-mock",
            "ds-mock", "qw-mock", "km-mock",
            "", "", "", "", "", "")


def run_full(force_restart):
    outs = list(app.ui_full_run(NAME, "p", "h", "a", "r", "m", "e", MATERIALS,
                                GOLD_BASIC, GOLD_ADV, force_restart, *API_ARGS))
    return "\n".join(outs), outs[-1]


# ================= 测试 B：断点恢复 =================
print("=" * 30, "测试 B：生成断点恢复", "=" * 30)
n_gen["calls"].clear(); n_gen["n"] = 0
final_all, final = run_full(False)

assert "♻️ 检测到断点" in final, "缺断点提示"
assert "已生成 143 条回答" in final, "断点回答数显示错误"
assert "已评分 0 条" in final
assert final.count("断点跳过（已完成 3/3）") == 47, \
    f"跳过单元数应为 47，实际 {final.count('断点跳过（已完成 3/3）')}"
assert "（续生成 1 次）" in final, "缺续生成提示"
assert n_gen["n"] == 1 and n_gen["calls"] == [("Kimi", 1)], \
    f"生成调用应恰 1 次且 n_valid=1，实际 {n_gen['calls']}"
assert "✅ 生成完成（144/144）" in final, "缺生成完成计数"
assert n_score["n"] == 1 and "✅ 评测完成" in final
st = json.load(open(rs_path, encoding="utf-8"))
assert st["status"] == "done", f"runstate 终态应为 done，实际 {st['status']}"
total_ans = sum(len(t) for bm in st["answers"].values() for t in bm.values())
assert total_ans == 144, f"落盘回答应 144 条，实际 {total_ans}"
print("✅ 测试 B 通过：47 单元跳过 + 续生成 1 条 = 144/144，runstate 终态 done")

# ================= 测试 C：强制重新开始 =================
print("=" * 30, "测试 C：强制重新开始", "=" * 30)
# 重新放入同指纹断点（状态 generating），再用 force_restart=True 跑
state2 = {"fingerprint": fp, "status": "generating", "answers": answers,
          "judge_raw": {}, "consistency": {}}
json.dump(state2, open(rs_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
n_gen["calls"].clear(); n_gen["n"] = 0
final_all, final = run_full(True)

assert "已勾选强制重新开始" in final, "缺强制重开提示"
assert "♻️ 检测到断点" not in final, "强制重开不应读取断点"
assert final.count("断点跳过") == 0, "强制重开不应跳过任何单元"
assert n_gen["n"] == 48 and all(v == 3 for _, v in n_gen["calls"]), \
    f"应 48 单元全部重生成（各 3 条），实际 {n_gen['calls'][:5]}…"
assert "✅ 生成完成（144/144）" in final
assert "✅ 评测完成" in final
print("✅ 测试 C 通过：强制重新开始忽略断点，48 单元 × 3 条全部重生成")

print("\n全部断点续跑测试通过 ✔")
