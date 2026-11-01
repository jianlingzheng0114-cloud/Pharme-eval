# -*- coding: utf-8 -*-
"""从断点 runstate 导出 ③ 纯计算模式可直接上传的评分表（xlsx，3次重复格式）。

用途：测评已完成（或部分完成）时，不重新调 API，直接把真实折算总分
（与"01_评分明细_单次回答"同口径，一票否决计 0）导出为纯计算导入表。

支持合并多个 runstate（如主断点 + 某模型的归档/补跑断点）：
  python tools/export_compute_table.py
  python tools/export_compute_table.py --states A_runstate.json B_runstate.json --out 表.xlsx

注意：若各 runstate 的指纹（材料/要素条件）不同，属混合实验条件，
脚本会在第 2 个工作表"导出说明"中如实标注，纯计算页只读第 1 个工作表。
"""
import argparse
import json
import os
import sys

TOOL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOL_DIR)
import pharme_eval as core  # noqa: E402
from make_compute_templates import COMBOS, RUNS_HEADER, _style  # noqa: E402

from openpyxl import Workbook  # noqa: E402


def merge_states(paths):
    """合并多个 runstate：answers 按 模型->列表 合并，judge_raw/consistency 按键合并（先到先得）"""
    merged = {"answers": {}, "judge_raw": {}, "consistency": {}, "model_status": {}}
    fps = set()
    for p in paths:
        st = json.load(open(p, encoding="utf-8"))
        fps.add(st.get("fingerprint", "?"))
        for combo, bm in st.get("answers", {}).items():
            tgt = merged["answers"].setdefault(combo, {})
            for m, texts in bm.items():
                if len(texts) > len(tgt.get(m, [])):   # 同键取更长者（主断点可能留有空占位列表）
                    tgt[m] = texts
        merged["judge_raw"].update(st.get("judge_raw", {}))
        merged["consistency"].update(st.get("consistency", {}))
        merged["model_status"].update(st.get("model_status", {}))
    return merged, fps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="场景名", help="场景名（默认读 outputs/<场景名>_runstate.json）")
    ap.add_argument("--states", nargs="+", default=None,
                    help="一个或多个 runstate json（缺省按 --scenario 自动定位）")
    ap.add_argument("--snapshot", default=None,
                    help="场景快照 json（缺省取 outputs/ 下该场景最新 *_scenario_*.json）")
    ap.add_argument("--out", default=None, help="输出 xlsx 路径")
    args = ap.parse_args()

    out_dir = os.path.join(TOOL_DIR, "outputs")
    states = args.states or [os.path.join(out_dir, f"{args.scenario}_runstate.json")]
    if args.snapshot:
        snapshot = args.snapshot
    else:
        import glob
        cands = sorted(glob.glob(os.path.join(out_dir, f"{args.scenario}_scenario_*.json")))
        if not cands:
            print(f"未找到场景快照 outputs/{args.scenario}_scenario_*.json，请用 --snapshot 指定")
            sys.exit(1)
        snapshot = cands[-1]
    out = args.out or os.path.join(out_dir, f"{args.scenario}_纯计算导入表.xlsx")
    for p_ in states + [snapshot]:
        if not os.path.exists(p_):
            print(f"文件不存在：{p_}")
            sys.exit(1)

    scenario_data = json.load(open(snapshot, encoding="utf-8"))
    merged, fps = merge_states(states)
    detail, missing = core.build_scores_detail(merged, scenario_data)
    if missing:
        print(f"⚠️ 缺 {len(missing)} 条评分（如 {missing[:3]}），对应格子留空会被纯计算拒收，请补齐后再导出")
        sys.exit(1)
    if len(detail) != 144:
        print(f"⚠️ 评分仅 {len(detail)}/144 条（模型×组合×次数不齐），不能导出完整纯计算表")
        sys.exit(1)

    wb = Workbook()
    ws = wb.active
    ws.title = "3次重复格式"
    ws.append(RUNS_HEADER)
    for combo in COMBOS:
        row = [combo]
        for m in ("DeepSeek", "Qwen", "Kimi"):
            for r in range(3):
                row.append(round(detail[(combo, m, r)][1], 2))
        ws.append(row)
    _style(ws, 10, 17)

    ws2 = wb.create_sheet("导出说明")
    ws2.append(["项目", "内容"])
    ws2.append(["数据来源", "；".join(os.path.basename(p) for p in states)])
    ws2.append(["口径", "折算总分 0–100（8 指标加权 + 一致性组层赋分），一票否决计 0，"
                        "与全量导出 xlsx『01_评分明细_单次回答』完全同口径"])
    ws2.append(["数据链条", "144 条单次回答效用（16 组合 × 3 模型 × 3 次）"])
    ws2.append(["指纹", "；".join(sorted(fps)) + ("（⚠️ 多指纹=混合实验条件，慎用！)" if len(fps) > 1 else "（单一条件）")])
    for m in ("DeepSeek", "Qwen", "Kimi"):
        n_cells = sum(1 for (_c, mm, _r) in detail if mm == m)
        ws2.append([f"{m} 条数", f"{n_cells} 条评分"])
    _style(ws2, 2, ws2.max_row)

    wb.save(out)
    print(f"已导出：{out}")
    print(f"评分条数：{len(detail)} | 指纹：{sorted(fps)}")


if __name__ == "__main__":
    main()
