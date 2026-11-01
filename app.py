# -*- coding: utf-8 -*-
"""
PHARM-E 提示词要素测评工具 —— 本地交互界面（Gradio）
====================================================
启动方式（在托管 venv 下）：
  ...\\envs\\default\\Scripts\\python.exe app.py
浏览器打开 http://127.0.0.1:7860

页面顶部为独立的“场景选择”下拉框（五个预设场景槽位，内容为空白模板，
六要素提示词与金标准由用户测试前自行填写，金标准分基础得分点/进阶得分点两栏，每行一条）。

五个功能页：
  ① 分模型测评 + 合成：填写/编辑案例提示词要素与金标准 → 16组合装配 → 三个模型按钮各自
     3次×16组合生成 + 评分（逐条落盘可续跑）→ "合成最终结果"按钮汇总
     → 144→48→16 聚合 → Shapley + bootstrap 95%CI → xlsx 报告（保留一键全跑兼容；
     开跑前校验六要素与基础得分点已填写）
  ② 已有回答评分：上传 answers.json（或逐条粘贴）→ 裁判评分 → 计算
  ③ 纯计算：上传表1样式评分表（xlsx/csv）→ 直接算 Shapley（无需 API）
  ④ 自检与演示：共识表1→表2 回归校验、合成数据演示
  ⑤ 已有结果 / 断点状态：只读展示后台已落盘数据（三模型进度、评分明细、回答原文、
     备份与文件清单、Shapley 预览），自动刷新，随时确认"结果确实存在"
"""
import hashlib
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pharme_eval as core  # noqa: E402
import retrieval as R  # noqa: E402

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(TOOL_DIR, "config.json")
SCENARIO_DIR = os.path.join(TOOL_DIR, "scenarios")
OUT_DIR = os.path.join(TOOL_DIR, "outputs")

DEFAULT_CONFIG = {
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "api_key": "",
    "judge_model": "qwen3-max",
    "gen_models": {"DeepSeek": "deepseek-v3", "Qwen": "qwen3-32b", "Kimi": "kimi-k2"},
    "gen_endpoints": {},  # 可选：{"DeepSeek": {"base_url": "...", "api_key": "..."}}
    "temperature_gen": 0.6,
    "top_p_gen": 1.0,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            cfg.update(json.load(open(CONFIG_PATH, encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    json.dump(cfg, open(CONFIG_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def list_scenarios():
    if not os.path.isdir(SCENARIO_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(SCENARIO_DIR) if f.endswith(".json"))


def load_scenario(name):
    return json.load(open(os.path.join(SCENARIO_DIR, name + ".json"), encoding="utf-8"))


def gold_markdown(sd):
    gold = sd.get("gold", {})
    lines = [f"**场景**：{sd.get('scenario', '')}", "",
             f"**基础得分点**（{len(gold.get('basic_points', []))} 条）："]
    lines += [f"{i+1}. {p}" for i, p in enumerate(gold.get("basic_points", []))]
    lines.append("")
    lines.append(f"**进阶得分点**（{len(gold.get('advanced_points', []))} 条）：")
    lines += [f"{i+1}. {p}" for i, p in enumerate(gold.get("advanced_points", []))]
    return "\n".join(lines)


def build_api_cfg(judge_base, judge_key, judge_model,
                  ds_model, qwen_model, kimi_model,
                  ds_base, ds_key, qwen_base, qwen_key, kimi_base, kimi_key):
    cfg = {
        "base_url": judge_base.strip(),
        "api_key": judge_key.strip(),
        "judge_model": judge_model.strip(),
        "gen_models": {},
        "temperature_gen": 0.6,
        "top_p_gen": 1.0,
    }
    for key, mid, burl, akey in [
        ("DeepSeek", ds_model, ds_base, ds_key),
        ("Qwen", qwen_model, qwen_base, qwen_key),
        ("Kimi", kimi_model, kimi_base, kimi_key),
    ]:
        mid = mid.strip()
        if burl.strip() or akey.strip():
            cfg["gen_models"][key] = {"model": mid,
                                      "base_url": burl.strip() or None,
                                      "api_key": akey.strip() or None}
        else:
            cfg["gen_models"][key] = mid
    return cfg


def result_markdown(res):
    phi = res["phi"]
    eff = res["eff"]
    lines = ["### 计算结果", "",
             "| 提示词元素 | SHAP值 | 贡献 |",
             "|---|---|---|"]
    for i in sorted(core.TESTED_ELEMENTS, key=lambda x: -phi[x]):
        val = round(phi[i], 2)
        tag = "正向增益" if val > 0 else ("负向干扰" if val < 0 else "贡献有限")
        lines.append(f"| {i} | {val} | {tag} |")
    ci = res.get("ci")
    if ci:
        lines += ["", "**bootstrap 95%CI**：" +
                  "；".join(f"{i}=({ci[i][0]}, {ci[i][1]})" for i in core.TESTED_ELEMENTS)]
    lines += ["", f"**效率性校验**：Σφ={eff['sum_phi']}，v(全联盟)−v(空联盟)={eff['v_full_minus_empty']}，"
                  f"差值={eff['diff']}（应≈0）"]
    if "chain" in res and res["chain"]:
        ch = res["chain"]
        lines += ["", f"**数据链条**：单次回答效用 {ch['单次回答效用条数']} 条 → "
                      f"模型层效用 {ch['模型层效用值个数']} 个 → 联盟效用 {ch['联盟效用值个数']} 个"]
    if "report" in res:
        lines += ["", f"**报告已生成**：`{res['report']}`"]
    return "\n".join(lines)


# ---------------- 交互动作 ----------------
def _parse_points(text):
    """金标准文本 → 得分点列表（每行一条，忽略空行）"""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def _gold_from_inputs(gold_basic, gold_adv, base_gold=None):
    """界面两栏金标准文本 → gold 字典（基础/进阶得分点）"""
    base_gold = base_gold or {}
    return {
        "basic_points": _parse_points(gold_basic),
        "advanced_points": _parse_points(gold_adv),
        "advanced_absent_rule": base_gold.get(
            "advanced_absent_rule",
            "若该场景金标准明确无进阶得分点，进阶得分点覆盖度记0分（本场景未触发）"),
    }


def ui_scenario_changed(name):
    """场景下拉框切换：载入该场景已保存内容（预设为空白模板，由用户自行填写）"""
    if not name:
        return [""] * 6 + ["", "", "", ""]
    sd = load_scenario(name)
    els = sd.get("elements", {})
    gold = sd.get("gold", {})
    query = "\n".join(R.extract_cited_titles(els.get("E", "")))
    return ([els.get(e, "") for e in core.ELEMENT_ORDER] +
            [sd.get("materials", "") or "",
             "\n".join(gold.get("basic_points", [])),
             "\n".join(gold.get("advanced_points", [])),
             query])


def ui_save_scenario(name, p, h, a, r, m, e, materials, gold_basic, gold_adv):
    """把当前六要素 + 材料 + 金标准保存回场景文件（供②页评分、⑤页显示与下次载入）"""
    if not name:
        return "❌ 请先在顶部下拉框选择场景。"
    try:
        data = _scenario_data(name, p, h, a, r, m, e, materials, gold_basic, gold_adv)
        os.makedirs(SCENARIO_DIR, exist_ok=True)
        path = os.path.join(SCENARIO_DIR, name + ".json")
        json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        n_b = len(data["gold"]["basic_points"])
        n_a = len(data["gold"]["advanced_points"])
        return f"✅ 已保存到 `scenarios/{name}.json`：六要素 + 材料 + 金标准（基础 {n_b} 条 / 进阶 {n_a} 条）。"
    except Exception as ex:
        return f"❌ 保存出错：{type(ex).__name__}: {ex}"


def _clean_materials(text):
    """去掉未粘贴的材料占位说明，避免占位文字进入提示词"""
    return "" if core.is_placeholder_materials(text) else (text or "")


def ui_evidence_from_files(files, materials, name):
    """E 元素循证依据：附件解析（政策/指南/共识原文，最高效力证据）"""
    if not files:
        return materials, "❌ 请先上传附件（pdf/docx/txt/md/html）"
    try:
        cards, errors = R.cards_from_files(files)
        if not cards:
            return materials, "❌ 附件解析未获得内容：" + "；".join(errors)
        block = R.build_evidence_text(cards, "附件解析")
        merged = R.merge_evidence_block(_clean_materials(materials), block)
        ev_path = R.save_evidence(OUT_DIR, name or "E证据", "附件解析", [], cards, errors)
        msg = f"✅ 已解析 {len(cards)} 份附件证据，并入固定任务材料（16 组合保持一致）。存档：`{ev_path}`"
        if errors:
            msg += "\n\n⚠️ " + "；".join(errors)
        return merged, msg
    except Exception as ex:
        return materials, f"❌ 运行出错：{type(ex).__name__}: {ex}"


def ui_evidence_from_search(query_text, sources, materials, name):
    """E 元素循证依据：无附件时的系统检索（T1→T2→T3 分级回退，检索结果冻结存档）"""
    queries = [q.strip() for q in (query_text or "").splitlines() if q.strip()]
    if not queries:
        return materials, ("❌ 检索式为空。E 元素中用《》标注的法规/指南/文献名会自动提取为检索式，"
                           "也可手动输入（每行一条）。")
    try:
        cfg = load_config().get("search", {})
        entries, errors = R.run_retrieval(queries, sources or None, cfg)
        if not entries:
            return materials, ("❌ 未检索到证据。" + "；".join(errors[:5]) +
                               "\n\n建议：改写/放宽检索式、勾选更多检索源，"
                               "或直接上传政策/指南/共识原文附件（最高效力）。")
        block = R.build_evidence_text(entries, "自动检索")
        merged = R.merge_evidence_block(_clean_materials(materials), block)
        ev_path = R.save_evidence(OUT_DIR, name or "E证据", "自动检索", queries, entries, errors)
        msg = f"✅ 检索到 {len(entries)} 条证据（已去重），并入固定任务材料（16 组合保持一致）。存档：`{ev_path}`"
        warn = [e for e in errors if "T3" in e or "失败" in e or "扩展" in e]
        if warn:
            msg += "\n\n⚠️ " + "；".join(warn[:5])
        return merged, msg
    except Exception as ex:
        return materials, f"❌ 运行出错：{type(ex).__name__}: {ex}"


def ui_preview(name, p, h, a, r, m, e, materials):
    elements = {"P": p, "H": h, "A": a, "R": r, "M": m, "E": e}
    combos = core.build_combinations(elements, materials)
    out = [f"共 {len(combos)} 个组合（H+A 固定，{{P,R,M,E}} 全子集，拼接顺序 P–H–A–R–M–E）：", ""]
    for cname, prompt, _ in combos[:3]:
        out.append(f"━━━ 组合 {cname}（前 200 字）━━━")
        out.append(prompt[:200] + ("…" if len(prompt) > 200 else ""))
        out.append("")
    out.append("（其余组合同理，运行时全部装配）")
    return "\n".join(out)


def _ts(t0):
    s = int(time.time() - t0)
    return f"[{s // 60:02d}:{s % 60:02d}]"


def _logline(msg):
    """运行日志落盘（outputs/运行日志.txt）：无论网页连接是否中断，事后都可追查“为什么没跑起来”"""
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(os.path.join(OUT_DIR, "运行日志.txt"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


_STATE_LOCK = threading.Lock()
_STATE_CACHE = {}   # runstate 路径 -> 状态 dict（进程内共享，三模型按钮可并行互不覆盖）


def _get_state(rs_path, fp, force_restart):
    """取断点状态：指纹一致即复用（含已完成单元缓存）；force_restart 或指纹不符则新建。
    已 done 的状态视为完成即失效（下一轮全新开始）。
    新建前若有历史数据，先自动备份为 *_旧备份_*.json 并在新状态记 _backup_note（杜绝静默丢失）。"""
    with _STATE_LOCK:
        st = _STATE_CACHE.get(rs_path)
        if st is not None and not force_restart \
                and st.get("fingerprint") == fp and st.get("status") != "done":
            return st
        st = None
        if not force_restart and os.path.exists(rs_path):
            try:
                prev = json.load(open(rs_path, encoding="utf-8"))
                if prev.get("fingerprint") == fp and prev.get("status") != "done":
                    st = prev
            except Exception:
                pass
        if st is None:
            # ---- 旧数据先备份再替换（防止误勾“强制重新开始”/指纹变化导致数据被静默覆盖）----
            backup_note = None
            old = _STATE_CACHE.get(rs_path)
            if old is None and os.path.exists(rs_path):
                try:
                    old = json.load(open(rs_path, encoding="utf-8"))
                except Exception:
                    old = None
            if old:
                n_ans = sum(len(v) for cm in old.get("answers", {}).values() for v in cm.values())
                n_judge = len(old.get("judge_raw", {}))
                if n_ans or n_judge:
                    bak = rs_path.replace(".json", f"_旧备份_{time.strftime('%Y%m%d_%H%M%S')}.json")
                    with open(bak, "w", encoding="utf-8") as f:
                        json.dump(old, f, ensure_ascii=False, indent=1)
                    backup_note = (f"⚠️ 检测到历史断点数据（{n_ans} 条回答、{n_judge} 条评分）将被新一轮替换，"
                                   f"已自动备份到 {os.path.basename(bak)}，数据未丢失。"
                                   f"若本不该重来，请立即停止并检查“强制重新开始”是否被勾选。")
                    # ---- 指纹不符且未勾选强制重启 → 中止，绝不静默作废历史成果 ----
                    if not force_restart and old.get("fingerprint") != fp:
                        raise RuntimeError(
                            f"⚠️ 已中止，历史数据未被覆盖：本次的要素/材料与历史断点不一致"
                            f"（材料或要素文本变了，最常见是重启后材料框没恢复原内容），"
                            f"而历史数据含 {n_ans} 条回答、{n_judge} 条评分。"
                            f"已自动备份到 {os.path.basename(bak)}。请二选一："
                            f"①恢复与上次完全相同的六要素和材料内容后再点按钮 → 自动从断点续跑，已有成果不作废；"
                            f"②确认全部重来 → 勾选“强制重新开始”后再点按钮。")
            st = {"fingerprint": fp, "status": "generating", "answers": {},
                  "judge_raw": {}, "consistency": {}, "model_status": {}}
            if backup_note:
                st["_backup_note"] = backup_note
        _STATE_CACHE[rs_path] = st
        return st


def _save_runstate(path, state):
    """断点状态原子落盘（写临时文件后替换，防止写一半被杀导致状态损坏）"""
    with _STATE_LOCK:
        state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def ui_full_run(name, p, h, a, r, m, e, materials, gold_basic, gold_adv, force_restart, *api_args):
    try:
        api_cfg = build_api_cfg(*api_args)
        if not api_cfg["api_key"] and not any(
                isinstance(v, dict) and v.get("api_key") for v in api_cfg["gen_models"].values()):
            yield "❌ 请先填写 API Key（页面顶部 API 配置区或 config.json）"
            return
        problems = _check_filled(p, h, a, r, m, e, gold_basic)
        if problems:
            yield "❌ 测评前请先完成填写：\n" + "\n".join(f"   - {x}" for x in problems)
            return
        scenario_data = _scenario_data(name, p, h, a, r, m, e, materials, gold_basic, gold_adv)
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        sc_path = os.path.join(OUT_DIR, f"{name}_scenario_{stamp}.json")
        json.dump(scenario_data, open(sc_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

        t0 = time.time()
        log = [f"▶ 开始全自动评测：{name}"]
        _logline(f"[全跑] {log[0]}（{name}）")

        def add(msg):
            log.append(f"{_ts(t0)} {msg}")
            _logline(f"[全跑] {msg}")

        # ---- 断点检测：要素与材料指纹一致才可续跑 ----
        rs_path = os.path.join(OUT_DIR, f"{name}_runstate.json")
        fp = _fingerprint(scenario_data, materials)
        state = _get_state(rs_path, fp, force_restart)
        n_ans = sum(len(v) for cm in state.get("answers", {}).values() for v in cm.values())
        n_judge = len(state.get("judge_raw", {}))
        if not force_restart and (n_ans or n_judge):
            add(f"♻️ 检测到断点（状态：{state.get('status')}）：已生成 {n_ans} 条回答、"
                f"已评分 {n_judge} 条，自动从中断处继续。如需全部重来请勾选“强制重新开始”。")
        if force_restart and os.path.exists(rs_path):
            add("⚠️ 已勾选强制重新开始，忽略历史断点。")
        if state.get("_backup_note"):
            add(state["_backup_note"])

        # 0) API 体检：秒级发现密钥/端点问题，避免长跑无反馈"假死"
        add("开跑前 API 体检（裁判 + 3 个被测模型各 1 次最小调用）…")
        yield "\n".join(log)
        problems = core.preflight_api(api_cfg)
        if problems:
            add("❌ API 体检未通过，已中止（避免长时间无响应）：")
            for x in problems:
                log.append(f"      - {x}")
            log.append("      请检查页面顶部 API 配置或 config.json 后重试。")
            yield "\n".join(log)
            return
        add("✅ API 体检通过")

        combos = core.build_combinations(scenario_data["elements"], materials)
        n_cells = len(combos) * len(core.MODEL_KEYS)      # 48 个条件单元
        total = n_cells * core.N_RUNS                     # 144 条回答
        answers = state["answers"]
        done = sum(len(v) for cm in answers.values() for v in cm.values())
        add(f"生成阶段：{n_cells} 个条件单元（16 组合 × 3 模型）× 每单元 {core.N_RUNS} 次独立生成 = {total} 条回答"
            f"（断点已有 {done} 条，逐条落盘，误关网页可续跑）")
        yield "\n".join(log)
        for cname, prompt, _ in combos:
            cell_state = answers.setdefault(cname, {})
            for mk in core.MODEL_KEYS:
                have = cell_state.setdefault(mk, [])
                cell = f"{cname} × {core.MODEL_DISPLAY[mk]}"
                if len(have) >= core.N_RUNS:
                    add(f"● {cell}：断点跳过（已完成 {core.N_RUNS}/{core.N_RUNS}）")
                    yield "\n".join(log)
                    continue
                need = core.N_RUNS - len(have)
                add(f"● {cell}（续生成 {need} 次）" if have else f"● {cell}")
                yield "\n".join(log)
                for kind, payload in core.generate_answers_iter(api_cfg, prompt, mk, n_valid=need):
                    if kind == "progress":
                        add(f"    {payload}")
                    elif kind == "answer":
                        have.append(payload)
                        done += 1
                        _save_runstate(rs_path, state)
                        add(f"    ✓ 第 {len(have)}/{core.N_RUNS} 条已存档（累计 {done}/{total}）")
                    yield "\n".join(log)
        ans_path = os.path.join(OUT_DIR, f"{name}_生成回答_{stamp}.json")
        json.dump(answers, open(ans_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        add(f"✅ 生成完成（{done}/{total}），回答已存：{ans_path}")
        add(f"评分阶段：{total} 条单条评分 + {n_cells} 次一致性判断（组层面），裁判温度 0 盲法，逐条落盘可续评")
        state["status"] = "scoring"
        _save_runstate(rs_path, state)
        yield "\n".join(log)
        res = None
        for kind, payload in core.run_score_iter(sc_path, ans_path, api_cfg, OUT_DIR,
                                                state=state,
                                                checkpoint_cb=lambda: _save_runstate(rs_path, state)):
            if kind == "progress":
                add(f"    {payload}")
            else:
                res = payload
            yield "\n".join(log)
        state["status"] = "done"
        _save_runstate(rs_path, state)
        log.append("\n✅ 评测完成！\n\n" + result_markdown(res))
        yield "\n".join(log)
    except Exception as ex:
        _logline(f"[全跑] ❌ 运行出错：{type(ex).__name__}: {ex}")
        yield f"❌ 运行出错：{type(ex).__name__}: {ex}"
    except BaseException as ex:  # GeneratorExit / CancelledError：客户端断开时 Gradio 直接取消任务
        _logline(f"[全跑] ⚠️ 任务未完成即被中断（{type(ex).__name__}）：多半是网页连接断开/页面被关闭，"
                 f"Gradio 随之取消了任务（界面不会显示报错）。已完成部分逐条落盘，重新点击按钮会自动续跑。")
        raise


# ---------------- 分模型测评（三按钮）+ 合成 ----------------
def _scenario_data(name, p, h, a, r, m, e, materials, gold_basic="", gold_adv=""):
    """界面输入 → scenario_data。金标准取界面两栏（测试前用户自行填写），不再取磁盘旧值。"""
    try:
        base = load_scenario(name)
    except Exception:
        base = {}   # 场景文件不存在时用默认规则（如用户自行删除了场景文件）
    return {
        "scenario": name,
        "elements": {"P": p, "H": h, "A": a, "R": r, "M": m, "E": e},
        "materials": materials,
        "gold": _gold_from_inputs(gold_basic, gold_adv, base.get("gold", {})),
        "veto_criteria": base.get("veto_criteria", "危害严重性评为5（致命危害）"),
        "rules": base.get("rules", {}),
    }


def _check_filled(p, h, a, r, m, e, gold_basic):
    """测评开跑前校验：六要素与金标准（基础得分点）须由用户填写"""
    problems = []
    if not any((x or "").strip() for x in (p, h, a, r, m, e)):
        problems.append("六要素提示词全部为空——请先填写")
    if not _parse_points(gold_basic):
        problems.append("金标准·基础得分点未填写（每行一条）——裁判评分离了金标准无法工作")
    return problems


def _fingerprint(scenario_data, materials):
    # 占位符材料视同未填（空）：避免"提示语文本 vs 空文本"造成假指纹差异、误废弃断点
    return hashlib.md5((json.dumps(scenario_data["elements"], sort_keys=True, ensure_ascii=False)
                        + "||" + _clean_materials(materials)).encode("utf-8")).hexdigest()[:12]


def _ui_model_run(model_key, name, p, h, a, r, m, e, materials, gold_basic, gold_adv,
                  force_restart, *api_args):
    """分模型测评：生成该模型 16 组合 × 3 次回答并完成评分（48 单条 + 16 一致性），逐条落盘可续跑"""
    try:
        api_cfg = build_api_cfg(*api_args)
        if not api_cfg["api_key"] and not any(
                isinstance(v, dict) and v.get("api_key") for v in api_cfg["gen_models"].values()):
            yield "❌ 请先填写 API Key（页面顶部 API 配置区或 config.json）"
            return
        problems = _check_filled(p, h, a, r, m, e, gold_basic)
        if problems:
            yield "❌ 测评前请先完成填写：\n" + "\n".join(f"   - {x}" for x in problems)
            return
        disp = core.MODEL_DISPLAY[model_key]
        scenario_data = _scenario_data(name, p, h, a, r, m, e, materials, gold_basic, gold_adv)
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        sc_path = os.path.join(OUT_DIR, f"{name}_scenario_{stamp}.json")
        json.dump(scenario_data, open(sc_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

        t0 = time.time()
        log = [f"▶ 开始测评 {disp} 组合：16 组合 × 3 次 = 48 条回答 + 48 条单条评分 + 16 次一致性判断（共 112 次调用）"]
        _logline(f"[{disp}] {log[0]}")

        def add(msg):
            log.append(f"{_ts(t0)} {msg}")
            _logline(f"[{disp}] {msg}")

        rs_path = os.path.join(OUT_DIR, f"{name}_runstate.json")
        fp = _fingerprint(scenario_data, materials)
        state = _get_state(rs_path, fp, force_restart)
        answers = state["answers"]
        n_ans = sum(len(cm.get(model_key, [])) for cm in answers.values())
        n_judge = sum(1 for k in state.get("judge_raw", {}) if k.split("|")[1] == model_key)
        if not force_restart and (n_ans or n_judge):
            add(f"♻️ 检测到断点（状态：{state.get('status')}）：{disp} 已生成 {n_ans} 条回答、"
                f"已评分 {n_judge} 条，自动从中断处继续。如需全部重来请勾选“强制重新开始”。")
        if force_restart and os.path.exists(rs_path):
            add("⚠️ 已勾选强制重新开始，忽略历史断点。")
        if state.get("_backup_note"):
            add(state["_backup_note"])

        add(f"开跑前 API 体检（裁判 + {disp} 各 1 次最小调用）…")
        yield "\n".join(log)
        problems = core.preflight_api(api_cfg, only=model_key)
        if problems:
            add("❌ API 体检未通过，已中止（避免长时间无响应）：")
            for x in problems:
                log.append(f"      - {x}")
            log.append("      请检查页面顶部 API 配置或 config.json 后重试。")
            yield "\n".join(log)
            return
        add("✅ API 体检通过")

        combos = core.build_combinations(scenario_data["elements"], materials)
        total = len(combos) * core.N_RUNS          # 48 条回答
        done = sum(len(cm.get(model_key, [])) for cm in answers.values())
        add(f"生成阶段：{len(combos)} 个组合 × {core.N_RUNS} 次 = {total} 条回答"
            f"（断点已有 {done} 条，逐条落盘，误关网页可续跑）")
        yield "\n".join(log)
        for cname, prompt, _ in combos:
            have = answers.setdefault(cname, {}).setdefault(model_key, [])
            cell = f"{cname} × {disp}"
            if len(have) >= core.N_RUNS:
                add(f"● {cell}：断点跳过（已完成 {core.N_RUNS}/{core.N_RUNS}）")
                yield "\n".join(log)
                continue
            need = core.N_RUNS - len(have)
            add(f"● {cell}（续生成 {need} 次）" if have else f"● {cell}")
            yield "\n".join(log)
            for kind, payload in core.generate_answers_iter(api_cfg, prompt, model_key, n_valid=need):
                if kind == "progress":
                    add(f"    {payload}")
                elif kind == "answer":
                    have.append(payload)
                    done += 1
                    _save_runstate(rs_path, state)
                    add(f"    ✓ 第 {len(have)}/{core.N_RUNS} 条已存档（累计 {done}/{total}）")
                yield "\n".join(log)
        add(f"✅ 生成完成（{done}/{total}）")
        add(f"评分阶段：{total} 条单条评分 + {len(combos)} 次一致性判断（组层面），裁判温度 0 盲法，逐条落盘可续评")
        yield "\n".join(log)
        for kind, payload in core.score_state_iter(api_cfg, scenario_data, state,
                                                   model_keys=[model_key],
                                                   checkpoint_cb=lambda: _save_runstate(rs_path, state)):
            if kind == "progress":
                add(f"    {payload}")
            yield "\n".join(log)
        state.setdefault("model_status", {})[model_key] = "done"
        _save_runstate(rs_path, state)
        log.append(f"\n✅ {disp} 测评完成并已落盘！\n\n"
                   f"三个模型都完成后，点“✦ 合成最终结果”生成汇总评分明细与 Shapley 值。")
        yield "\n".join(log)
    except Exception as ex:
        _logline(f"[{model_key}] ❌ 运行出错：{type(ex).__name__}: {ex}")
        yield f"❌ 运行出错：{type(ex).__name__}: {ex}"
    except BaseException as ex:  # GeneratorExit / CancelledError：客户端断开时 Gradio 直接取消任务
        _logline(f"[{model_key}] ⚠️ 任务未完成即被中断（{type(ex).__name__}）：多半是网页连接断开/页面被关闭，"
                 f"Gradio 随之取消了任务（界面不会显示报错）。已完成部分逐条落盘，重新点击按钮会自动续跑。")
        raise


def ui_model_run_ds(*args):
    yield from _ui_model_run("DeepSeek", *args)


def ui_model_run_qw(*args):
    yield from _ui_model_run("Qwen", *args)


def ui_model_run_km(*args):
    yield from _ui_model_run("Kimi", *args)


def _load_state(rs_path, fp):
    """读取断点状态（进程内缓存优先，指纹须一致才复用），返回 state 或 None"""
    with _STATE_LOCK:
        st = _STATE_CACHE.get(rs_path)
        if st is not None and st.get("fingerprint") == fp:
            return st
    if os.path.exists(rs_path):
        try:
            prev = json.load(open(rs_path, encoding="utf-8"))
            if prev.get("fingerprint") == fp:
                return prev
        except Exception:
            pass
    return None


def ui_merge_run(name, p, h, a, r, m, e, materials, gold_basic, gold_adv):
    """合成最终结果：三模型汇总评分明细 + Shapley 值计算（纯本地计算，无需 API）"""
    try:
        scenario_data = _scenario_data(name, p, h, a, r, m, e, materials, gold_basic, gold_adv)
        rs_path = os.path.join(OUT_DIR, f"{name}_runstate.json")
        fp = _fingerprint(scenario_data, materials)
        state = _load_state(rs_path, fp)
        if state is None:
            return ("❌ 未找到测评数据（或要素/材料已改动导致指纹不符）。"
                    "请先分别点三个模型的测评按钮完成生成与评分，再合成。")
        res = core.merge_state(state, scenario_data, OUT_DIR)
        state["status"] = "done"
        _save_runstate(rs_path, state)
        return "✅ 合成完成！三模型 144 条回答已汇总。\n\n" + result_markdown(res)
    except Exception as ex:
        return f"❌ 运行出错：{type(ex).__name__}: {ex}"


def ui_export_all(name, p, h, a, r, m, e, materials, gold_basic, gold_adv):
    """导出按钮：全部评分明细 + 合成计算分析 + 原始数据 → zip（可下载；纯本地，无需 API）"""
    try:
        scenario_data = _scenario_data(name, p, h, a, r, m, e, materials, gold_basic, gold_adv)
        rs_path = os.path.join(OUT_DIR, f"{name}_runstate.json")
        fp = _fingerprint(scenario_data, materials)
        state = _load_state(rs_path, fp)
        if state is None:
            return None, ("❌ 未找到测评数据（或要素/材料已改动导致指纹不符）。"
                          "请先完成测评（或保持要素、材料与测评时一致）再导出。")
        zip_path, summary = core.export_all(state, scenario_data, OUT_DIR)
        _logline(f"[导出] {os.path.basename(zip_path)}")
        return zip_path, "✅ 导出完成！在上方文件框点击即可下载。\n\n" + summary
    except Exception as ex:
        return None, f"❌ 运行出错：{type(ex).__name__}: {ex}"


def ui_export_docx(name, p, h, a, r, m, e, materials, gold_basic, gold_adv):
    """附件导出：Word 文档（六节框架：场景分类/案例内容/金标准/模型评分明细/SHAP/推荐组合）"""
    try:
        scenario_data = _scenario_data(name, p, h, a, r, m, e, materials, gold_basic, gold_adv)
        rs_path = os.path.join(OUT_DIR, f"{name}_runstate.json")
        fp = _fingerprint(scenario_data, materials)
        state = _load_state(rs_path, fp)
        if state is None:
            return None, ("❌ 未找到测评数据（或要素/材料已改动导致指纹不符）。"
                          "请先完成测评（或保持要素、材料与测评时一致）再导出。")
        path_docx, summary = core.export_docx(state, scenario_data, OUT_DIR)
        _logline(f"[导出] {os.path.basename(path_docx)}")
        return path_docx, "✅ Word 附件已生成！在上方文件框点击即可下载。\n\n" + summary
    except Exception as ex:
        return None, f"❌ 运行出错：{type(ex).__name__}: {ex}"


def ui_score_run(name, answers_file, *api_args):
    try:
        api_cfg = build_api_cfg(*api_args)
        sc_path = os.path.join(SCENARIO_DIR, name + ".json")
        yield "▶ API 体检（裁判模型 1 次最小调用）…"
        problems = core.preflight_api(api_cfg, include_gen=False)
        if problems:
            yield "❌ API 体检未通过，已中止：\n" + "\n".join(f"   - {x}" for x in problems)
            return
        t0 = time.time()
        log = ["✅ 裁判模型连通", f"{_ts(t0)} ▶ 评分中…"]
        res = None
        for kind, payload in core.run_score_iter(sc_path, answers_file, api_cfg, OUT_DIR):
            if kind == "progress":
                log.append(f"{_ts(t0)}     {payload}")
            else:
                res = payload
            yield "\n".join(log)
        log.append("\n✅ 评分完成！\n\n" + result_markdown(res))
        yield "\n".join(log)
    except Exception as ex:
        yield f"❌ 运行出错：{type(ex).__name__}: {ex}"


def ui_compute_run(score_file, result_name):
    try:
        res = core.run_compute(score_file, result_name or "测评结果", OUT_DIR)
        return "✅ 计算完成！\n\n" + result_markdown(res)
    except Exception as ex:
        return f"❌ 运行出错：{type(ex).__name__}: {ex}"


def ui_validate():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = core.run_validate()
    return buf.getvalue() + ("\n✅ 校验通过" if ok else "\n❌ 存在差异")


def ui_demo():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = core.run_demo(OUT_DIR)
    return buf.getvalue() + "\n\n" + result_markdown(res) + \
        "\n\n⚠️ DEMO 为合成随机数据，仅用于验证工具流程，不可用于论文。"


def ui_api_test(*api_args):
    """API 连接测试：裁判 + 3 个被测模型逐个发最小调用，报告连通性、响应与耗时"""
    api_cfg = build_api_cfg(*api_args)
    if not api_cfg["api_key"] and not any(
            isinstance(v, dict) and v.get("api_key") for v in api_cfg["gen_models"].values()):
        return ("❌ 未填写 API Key：请先在上方填写（或编辑 config.json）再测试连接。"
                "注意：占位符 `sk-请填入你的API-Key` 不是有效密钥。")

    def _mid(model):
        return model["model"] if isinstance(model, dict) else str(model)

    def _host(model):
        base = (model.get("base_url") or api_cfg["base_url"]) if isinstance(model, dict) else api_cfg["base_url"]
        return re.sub(r"^https?://", "", (base or "").rstrip("/")).split("/")[0]

    targets = [(f"裁判模型 `{_mid(api_cfg['judge_model'])}`（{_host(api_cfg['judge_model'])}）",
                api_cfg["judge_model"])]
    for k, v in api_cfg["gen_models"].items():
        targets.append((f"被测模型 {core.MODEL_DISPLAY.get(k, k)} `{_mid(v)}`（{_host(v)}）", v))

    rows, n_ok = [], 0
    for label, model in targets:
        t0 = time.time()
        try:
            reply = core.chat_completion(api_cfg, model,
                                         [{"role": "user", "content": "请只回复两个字：正常"}],
                                         temperature=0.0, max_retries=1, timeout=30)
            dt = time.time() - t0
            n_ok += 1
            rows.append(f"| {label} | ✅ 连通 | {(reply or '').strip()[:20] or '（空回复）'} | {dt:.2f}s |")
        except Exception as ex:
            dt = time.time() - t0
            rows.append(f"| {label} | ❌ 失败 | {str(ex)[:110]} | {dt:.2f}s |")
    head = (f"### 连接测试结果：{n_ok}/{len(targets)} 通过\n\n"
            "| 模型（端点） | 状态 | 响应/错误 | 耗时 |\n|---|---|---|---|")
    tail = "" if n_ok == len(targets) else (
        "\n\n⚠️ 存在失败项，请逐项核对：① API Key 是否为真实密钥；"
        "② base_url 与模型是否匹配（如 deepseek-v3 应配 DeepSeek 官方端点、kimi-k2 应配 Moonshot）；"
        "③ 模型 ID 拼写。全部通过后即可点评测按钮。")
    return "\n".join([head] + rows + [tail])


# ---------------- ⑤ 已有结果 / 断点状态（只读展示，不调 API） ----------------
def _read_runstate(name):
    """读磁盘断点文件（落盘即事实，运行中每条都已原子写入）。返回 (state|None, rs_path)"""
    rs_path = os.path.join(OUT_DIR, f"{name}_runstate.json")
    if not os.path.exists(rs_path):
        return None, rs_path
    try:
        return json.load(open(rs_path, encoding="utf-8")), rs_path
    except Exception:
        return None, rs_path


def _combo_names():
    from itertools import combinations
    return ["+".join(core.BASE_ELEMENTS + list(sub))
            for r in range(len(core.TESTED_ELEMENTS) + 1)
            for sub in combinations(core.TESTED_ELEMENTS, r)]


def _model_counts(state, mk):
    answers = state.get("answers", {})
    n_ans = sum(len(bm.get(mk, [])) for bm in answers.values())
    n_judge = sum(1 for k in state.get("judge_raw", {}) if k.split("|")[1] == mk)
    n_cons = sum(1 for k in state.get("consistency", {}) if k.split("|")[1] == mk)
    return n_ans, n_judge, n_cons


def _cell_utils(state, has_adv):
    """已评条目 → {(combo, mk, run): (raw, cons, u|None, veto)}，纯本地折算。
    has_adv：当前金标准是否含进阶得分点（取界面金标准，与测评时一致）"""
    st_judge = state.get("judge_raw", {})
    st_cons = state.get("consistency", {})
    out = {}
    for jkey, jd in st_judge.items():
        parts = jkey.split("|")
        if len(parts) != 3:
            continue
        combo, mk, r = parts[0], parts[1], int(parts[2])
        raw = dict(jd.get("raw", {}))
        veto = bool(jd.get("veto", False))
        cons = st_cons.get(f"{combo}|{mk}")
        u = None
        if cons is not None:
            raw_full = dict(raw)
            raw_full["consistency"] = cons
            u = core.answer_utility(raw_full, has_advanced=has_adv, veto=veto)
        out[(combo, mk, r)] = (raw, cons, u, veto)
    return out


def ui_results_panel(name, p, h, a, r, m, e, materials, gold_basic="", gold_adv=""):
    """⑤ 已有结果/断点状态总览：进度表 + 16组合均分预览 + 评分明细（每条已评分回答一行）"""
    empty = ("", [], [], [])
    if not name:
        return ("请先在页面顶部下拉框选择场景。", [], [], [])
    try:
        state, rs_path = _read_runstate(name)
        if state is None:
            return (f"**尚无已落盘结果**：未找到断点文件 `{os.path.basename(rs_path)}`。\n\n"
                    "在①页点击任一测评按钮后，每生成 1 条回答、每评完 1 条都会立即存入该文件，"
                    "此页自动刷新可随时确认。", [], [], [])
        lines = []
        st = os.stat(rs_path)
        lines.append(f"### 📂 已落盘结果总览 —— {name}")
        lines.append("")
        lines.append(f"- **断点文件**：`outputs/{os.path.basename(rs_path)}`"
                     f"（{st.st_size / 1024:.0f} KB，最后写入 {state.get('updated_at', '—')}）")
        status_cn = {"generating": "生成/评分进行中", "scoring": "评分进行中", "done": "已完成（合成完毕）"}.get(
            state.get("status"), state.get("status") or "—")
        lines.append(f"- **状态**：{status_cn}")
        # 指纹核对：当前输入与断点是否一致（决定续跑是否有效）
        try:
            fp_now = _fingerprint(_scenario_data(name, p, h, a, r, m, e, materials,
                                                 gold_basic, gold_adv), materials)
            fp = state.get("fingerprint", "")
            if fp == fp_now:
                lines.append(f"- **指纹校验**：✅ 当前页面的六要素+材料与断点一致（`{fp}`），测评按钮将**自动续跑**。")
            else:
                lines.append(f"- **指纹校验**：⚠️ 当前输入与断点（`{fp}`）**不一致**——直接点测评按钮会视为新任务重来"
                             f"（旧数据将自动备份为 `*_旧备份_*.json`，不会丢）。想续跑请把①页要素/材料恢复原内容。")
        except Exception:
            pass
        # 合成就绪度
        gaps = core.check_merge_ready(state)
        if not gaps:
            lines.append("- **合成就绪度**：✅ 三模型均已齐全，可点“✦ 合成最终结果”。")
        else:
            lines.append("- **合成就绪度**：⏳ 尚缺 —— " + "；".join(gaps))
        # 备份文件
        import glob
        baks = sorted(glob.glob(os.path.join(OUT_DIR, f"{name}_runstate_旧备份_*.json")))
        if baks:
            lines.append(f"- **旧备份文件**（{len(baks)} 个，数据被替换前自动留存）：")
            for b in baks[-3:]:
                lines.append(f"    - `{os.path.basename(b)}`")
        lines.append("")
        # ---- 进度表 ----
        progress = []
        for mk in core.MODEL_KEYS:
            n_ans, n_judge, n_cons = _model_counts(state, mk)
            done_flag = state.get("model_status", {}).get(mk) == "done"
            flag = "✅ 完成" if done_flag else ("🔄 进行中" if (n_ans or n_judge) else "⬜ 未开始")
            progress.append([core.MODEL_DISPLAY[mk],
                             f"{n_ans}/{16 * core.N_RUNS}", f"{n_judge}/{16 * core.N_RUNS}",
                             f"{n_cons}/16", flag])
        tot = [sum(_model_counts(state, mk)[i] for mk in core.MODEL_KEYS) for i in range(3)]
        progress.append(["合计", f"{tot[0]}/{16 * core.N_RUNS * 3}", f"{tot[1]}/{16 * core.N_RUNS * 3}",
                         f"{tot[2]}/48", ""])
        # ---- 16组合均分预览 ----
        utils = _cell_utils(state, bool(_parse_points(gold_adv)))
        model_level = {}
        for (combo, mk, r), (_raw, _c, u, _v) in utils.items():
            if u is not None:
                model_level.setdefault((combo, mk), []).append(u)
        combo_rows = []
        for cname in _combo_names():
            row = [cname]
            us = []
            for mk in core.MODEL_KEYS:
                vals = model_level.get((cname, mk))
                if vals:
                    mu = sum(vals) / len(vals)
                    us.append(mu)
                    row.append(round(mu, 2))
                else:
                    row.append("—")
            n_done = sum(len(model_level.get((cname, mk), [])) for mk in core.MODEL_KEYS)
            row.append(round(sum(us) / len(us), 2) if us else "—")
            row.append(f"{n_done}/{3 * core.N_RUNS}")
            combo_rows.append(row)
        # ---- 评分明细 ----
        detail_rows = []
        order = {c: i for i, c in enumerate(_combo_names())}
        for (combo, mk, r), (raw, cons, u, veto) in sorted(
                utils.items(), key=lambda kv: (order.get(kv[0][0], 99),
                                               core.MODEL_KEYS.index(kv[0][1]) if kv[0][1] in core.MODEL_KEYS else 9,
                                               kv[0][2])):
            row = [combo, core.MODEL_DISPLAY[mk], r + 1]
            for f in core.IND_FIELDS:
                v = cons if f == "consistency" else raw.get(f)
                row.append("—" if v is None else v)
            row.append("—" if u is None else round(u, 2))
            row.append("是" if veto else "否")
            detail_rows.append(row)
        return "\n".join(lines), progress, combo_rows, detail_rows
    except Exception as ex:
        return (f"❌ 读取出错：{type(ex).__name__}: {ex}", [], [], [])


def ui_results_answer(name, combo, model_disp):
    """查看某组合×模型已落盘的回答原文（及对应评分摘要）"""
    if not name or not combo:
        return "请选择场景与提示词组合。"
    try:
        state, _ = _read_runstate(name)
        if state is None:
            return "（尚无落盘数据）"
        mk = next((k for k, v in core.MODEL_DISPLAY.items() if v == model_disp), None)
        texts = state.get("answers", {}).get(combo, {}).get(mk, [])
        if not texts:
            return f"（{combo} × {model_disp} 尚无已落盘回答）"
        parts = [f"━━━ {combo} × {model_disp}：已落盘 {len(texts)}/{core.N_RUNS} 条 ━━━"]
        for i, t in enumerate(texts):
            parts.append(f"\n────── 第 {i + 1} 条回答 ──────\n{t}")
            j = state.get("judge_raw", {}).get(f"{combo}|{mk}|{i}")
            if j:
                raw = j.get("raw", {})
                sc = "、".join(f"{core.FIELD_CN[f]}={raw.get(f)}" for f in core.IND_FIELDS if f != "consistency")
                cons = state.get("consistency", {}).get(f"{combo}|{mk}")
                parts.append(f"\n【评分】{sc}；一致性={'—' if cons is None else cons}；"
                             f"一票否决={'是' if j.get('veto') else '否'}")
        return "\n".join(parts)
    except Exception as ex:
        return f"❌ 读取出错：{type(ex).__name__}: {ex}"


# ---------------- 界面 ----------------
def build_ui():
    import gradio as gr
    cfg = load_config()
    scen_names = list_scenarios()
    default_scen = scen_names[0] if scen_names else None

    with gr.Blocks(title="PHARM-E 提示词要素测评工具") as demo:
        gr.Markdown("# PHARM-E 提示词要素测评工具\n"
                    "依据《五大核心场景的测评方法》：16 组合装配 → 生成 → 裁判评分 → "
                    "144→48→16 聚合 → Shapley + bootstrap 95%CI → 表1/表2 报告")

        with gr.Accordion("API 配置（裁判 + 3 个被测模型）", open=False):
            gr.Markdown("留空的独立端点将回退到默认端点。可点“保存配置”写入 config.json。")
            judge_base = gr.Textbox(label="默认 base_url（OpenAI 兼容）", value=cfg["base_url"])
            judge_key = gr.Textbox(label="默认 api_key", value=cfg["api_key"], type="password")
            judge_model = gr.Textbox(label="裁判模型 ID", value=cfg["judge_model"])
            gm = cfg.get("gen_models", DEFAULT_CONFIG["gen_models"])
            ge = cfg.get("gen_endpoints", {})
            with gr.Row():
                ds_model = gr.Textbox(label="被测模型 DeepSeek-V3 ID", value=str(gm.get("DeepSeek", "deepseek-v3")))
                qwen_model = gr.Textbox(label="被测模型 Qwen3-32B ID", value=str(gm.get("Qwen", "qwen3-32b")))
                kimi_model = gr.Textbox(label="被测模型 Kimi-K2 ID", value=str(gm.get("Kimi", "kimi-k2")))
            with gr.Accordion("被测模型独立端点（可选，留空用默认）", open=False):
                with gr.Row():
                    ds_base = gr.Textbox(label="DeepSeek base_url", value=ge.get("DeepSeek", {}).get("base_url", ""))
                    ds_key = gr.Textbox(label="DeepSeek api_key", value=ge.get("DeepSeek", {}).get("api_key", ""), type="password")
                with gr.Row():
                    qwen_base = gr.Textbox(label="Qwen base_url", value=ge.get("Qwen", {}).get("base_url", ""))
                    qwen_key = gr.Textbox(label="Qwen api_key", value=ge.get("Qwen", {}).get("api_key", ""), type="password")
                with gr.Row():
                    kimi_base = gr.Textbox(label="Kimi base_url", value=ge.get("Kimi", {}).get("base_url", ""))
                    kimi_key = gr.Textbox(label="Kimi api_key", value=ge.get("Kimi", {}).get("api_key", ""), type="password")
            api_fields = [judge_base, judge_key, judge_model,
                          ds_model, qwen_model, kimi_model,
                          ds_base, ds_key, qwen_base, qwen_key, kimi_base, kimi_key]

            def _save(jb, jk, jm, dsm, qm, km, db, dk, qb, qk, kb, kk):
                c = load_config()
                c["base_url"], c["api_key"], c["judge_model"] = jb.strip(), jk.strip(), jm.strip()
                c["gen_models"] = {"DeepSeek": dsm.strip(), "Qwen": qm.strip(), "Kimi": km.strip()}
                c["gen_endpoints"] = {}
                for k, b, a in [("DeepSeek", db, dk), ("Qwen", qb, qk), ("Kimi", kb, kk)]:
                    if b.strip() or a.strip():
                        c["gen_endpoints"][k] = {"base_url": b.strip(), "api_key": a.strip()}
                save_config(c)
                return "✅ 已保存到 config.json"

            save_btn = gr.Button("保存配置到 config.json")
            save_msg = gr.Markdown()
            save_btn.click(_save, api_fields, save_msg)
            test_btn = gr.Button("连接测试（裁判 + 3 个被测模型逐个实测）", variant="secondary")
            test_msg = gr.Markdown()
            test_btn.click(ui_api_test, api_fields, test_msg)

        # ---------- 场景选择（单独下拉框，全页共用） ----------
        with gr.Row():
            scen_dd = gr.Dropdown(choices=scen_names, value=default_scen,
                                  label="场景选择（单独下拉框）", scale=1)
            gr.Markdown("选择场景后载入该场景**已保存**的内容。预设为**空白模板**——"
                        "六要素提示词与金标准（基础/进阶得分点）**测试前请自行填写**，"
                        "可点①页“保存当前场景内容”写入场景文件。", scale=3)

        with gr.Tabs():
            # ---------- ① 全自动 ----------
            with gr.Tab("① 全自动评测"):
                with gr.Row():
                    el_p = gr.Textbox(label="P 任务目的（Purpose）", lines=4)
                    el_h = gr.Textbox(label="H 场景描述（Healthcare Context）", lines=4)
                with gr.Row():
                    el_a = gr.Textbox(label="A 任务摘要（Abstract）", lines=4)
                    el_r = gr.Textbox(label="R 角色定位（Role）", lines=4)
                with gr.Row():
                    el_m = gr.Textbox(label="M 方法路径（Methodology）", lines=4)
                    el_e = gr.Textbox(label="E 循证依据与伦理准则（Evidence/Ethics）", lines=4)
                el_materials = gr.Textbox(label="固定任务材料（materials，随每条提示词下发）", lines=5)
                gr.Markdown("**金标准（裁判评分依据，测试前必填）**：基础得分点、进阶得分点各一栏，"
                            "**每行一条**；无进阶得分点可留空（进阶覆盖度按预设规则记 0 分）。"
                            "测评按钮开跑前会校验基础得分点非空。")
                with gr.Row():
                    gold_basic = gr.Textbox(label="金标准 · 基础得分点（每行一条，必填）", lines=6)
                    gold_adv = gr.Textbox(label="金标准 · 进阶得分点（每行一条，可空）", lines=6)
                save_scen_btn = gr.Button("💾 保存当前场景内容（六要素 + 材料 + 金标准 → scenarios/）")
                save_scen_msg = gr.Markdown()
                save_scen_btn.click(ui_save_scenario,
                                    [scen_dd, el_p, el_h, el_a, el_r, el_m, el_e, el_materials,
                                     gold_basic, gold_adv],
                                    save_scen_msg)
                ev_query = gr.Textbox(label="E 元素循证检索式（默认自动提取 E 元素《》引用，可修改；每行一条）", lines=3)
                scen_dd.change(ui_scenario_changed, scen_dd,
                               [el_p, el_h, el_a, el_r, el_m, el_e, el_materials,
                                gold_basic, gold_adv, ev_query])
                if default_scen:
                    vals = ui_scenario_changed(default_scen)
                    el_p.value, el_h.value, el_a.value, el_r.value, el_m.value, el_e.value = vals[:6]
                    el_materials.value = vals[6]
                    gold_basic.value, gold_adv.value, ev_query.value = vals[7], vals[8], vals[9]

                with gr.Accordion("E 元素循证依据（附件优先；无附件时保留系统检索）", open=False):
                    gr.Markdown(
                        "**效力顺序**：上传的政策/指南/共识原文（最高效力）＞系统检索结果"
                        "（T1 PubMed/CrossRef/OpenAlex → T2 Semantic Scholar → T3 政策网页，分级回退）。"
                        "生成的证据材料并入上方固定任务材料，在全部 16 种组合中保持一致，"
                        "并自动存档（含检索式与访问日期，可溯源）。")
                    ev_files = gr.Files(label="上传附件（政策/指南/共识原文，pdf/docx/txt/md/html，可多选）",
                                        file_types=[".pdf", ".docx", ".txt", ".md", ".html", ".htm"])
                    btn_ev_file = gr.Button("解析附件并生成证据材料")
                    ev_sources = gr.CheckboxGroup(
                        choices=["pubmed", "crossref", "openalex", "semantic_scholar", "policy"],
                        value=["pubmed", "crossref", "openalex", "policy"],
                        label="检索源（勾选；policy 为 T3 网页检索，须人工复核）")
                    btn_ev_search = gr.Button("检索并生成证据材料（无附件时使用）")
                    ev_status = gr.Markdown()
                    btn_ev_file.click(ui_evidence_from_files,
                                      [ev_files, el_materials, scen_dd], [el_materials, ev_status])
                    btn_ev_search.click(ui_evidence_from_search,
                                        [ev_query, ev_sources, el_materials, scen_dd],
                                        [el_materials, ev_status])

                preview_btn = gr.Button("预览 16 组合装配")
                preview_out = gr.Textbox(label="组合预览", lines=8)
                preview_btn.click(ui_preview,
                                  [scen_dd, el_p, el_h, el_a, el_r, el_m, el_e, el_materials],
                                  preview_out)

                force_restart = gr.Checkbox(
                    label="强制重新开始（忽略断点缓存；不勾选则误关网页后自动续跑）", value=False)
                gr.Markdown("**分模型测评**：三个按钮分别测评三个模型的 16 组合（各 112 次调用，等待时间约为整跑的 1/3），"
                            "逐条落盘、可随时中断续跑；三个都完成后点“合成最终结果”得到汇总评分明细与 Shapley 值。")
                with gr.Row():
                    btn_ds = gr.Button(f"① 测评 {core.MODEL_DISPLAY['DeepSeek']}（48 生成 + 64 裁判）")
                    btn_qw = gr.Button(f"② 测评 {core.MODEL_DISPLAY['Qwen']}（48 生成 + 64 裁判）")
                    btn_km = gr.Button(f"③ 测评 {core.MODEL_DISPLAY['Kimi']}（48 生成 + 64 裁判）")
                btn_merge = gr.Button("✦ 合成最终结果（汇总评分明细 + Shapley 值计算，无需 API）",
                                      variant="primary")
                with gr.Accordion("兼容：一键全跑（三模型串行，共 336 次调用）", open=False):
                    run_btn = gr.Button("▶ 开始全自动评测（48 条件单元 × 3 次 = 144 条回答 + 192 次裁判）")
                full_log = gr.Textbox(label="运行日志 / 结果（合成 / 一键全跑）", lines=18)
                with gr.Row():
                    ds_log = gr.Textbox(label=f"{core.MODEL_DISPLAY['DeepSeek']} 运行日志（独立，可与其他按钮并行）", lines=18)
                    qw_log = gr.Textbox(label=f"{core.MODEL_DISPLAY['Qwen']} 运行日志（独立，可与其他按钮并行）", lines=18)
                    km_log = gr.Textbox(label=f"{core.MODEL_DISPLAY['Kimi']} 运行日志（独立，可与其他按钮并行）", lines=18)
                gr.Markdown("提示：全程同步写入 `outputs/运行日志.txt`——即使网页断开导致任务停止，事后也能查到断在哪里；"
                            "已完成部分逐条落盘，重新点击按钮自动续跑。")
                _run_inputs = [scen_dd, el_p, el_h, el_a, el_r, el_m, el_e, el_materials,
                               gold_basic, gold_adv, force_restart] + api_fields
                btn_ds.click(ui_model_run_ds, _run_inputs, ds_log)
                btn_qw.click(ui_model_run_qw, _run_inputs, qw_log)
                btn_km.click(ui_model_run_km, _run_inputs, km_log)
                btn_merge.click(ui_merge_run,
                                [scen_dd, el_p, el_h, el_a, el_r, el_m, el_e, el_materials,
                                 gold_basic, gold_adv],
                                full_log)
                with gr.Row():
                    btn_export = gr.Button("📦 导出全部评分明细与分析（zip：xlsx + 摘要 + 原始数据）")
                    btn_export_docx = gr.Button("📄 附件导出（Word，六节框架）")
                export_file = gr.File(label="导出包（点击下载）", interactive=False)
                export_msg = gr.Markdown()
                btn_export.click(ui_export_all,
                                 [scen_dd, el_p, el_h, el_a, el_r, el_m, el_e, el_materials,
                                  gold_basic, gold_adv],
                                 [export_file, export_msg])
                btn_export_docx.click(ui_export_docx,
                                      [scen_dd, el_p, el_h, el_a, el_r, el_m, el_e, el_materials,
                                       gold_basic, gold_adv],
                                      [export_file, export_msg])
                run_btn.click(ui_full_run, _run_inputs, full_log)

            # ---------- ② 已有回答评分 ----------
            with gr.Tab("② 已有回答 → 评分"):
                gr.Markdown("answers.json 格式：`{\"H+A\": {\"DeepSeek\": [\"回答1\",\"回答2\",\"回答3\"], "
                            "\"Qwen\": [...], \"Kimi\": [...]}, \"H+A+P\": {...}, ...}`（16 个组合键）\n\n"
                            "场景使用**页面顶部下拉框**当前选定项；金标准与评分规则取该场景文件"
                            "（请先在①页填写并保存）。")
                ans_file = gr.File(label="上传 answers.json", file_types=[".json"])
                score_btn = gr.Button("▶ 裁判评分并计算", variant="primary")
                score_out = gr.Textbox(label="运行结果", lines=18)
                score_btn.click(ui_score_run, [scen_dd, ans_file] + api_fields, score_out)

            # ---------- ③ 纯计算 ----------
            with gr.Tab("③ 纯计算（无需 API）"):
                gr.Markdown("上传表1样式评分表：第 1 列=提示词组合（如 `H+A`、`H+A+P`），"
                            "其后为 `DeepSeek`/`Qwen`/`Kimi` 三列（3 次均分），"
                            "或 `DeepSeek_1..3` 等 9 列单次分（可算 95%CI）。支持 xlsx/csv。")
                score_file = gr.File(label="上传评分表", file_types=[".xlsx", ".csv"])
                rname = gr.Textbox(label="结果名称（用于报告文件名）", value="测评结果")
                comp_btn = gr.Button("▶ 计算 Shapley 并生成报告", variant="primary")
                comp_out = gr.Textbox(label="运行结果", lines=18)
                comp_btn.click(ui_compute_run, [score_file, rname], comp_out)

            # ---------- ④ 自检 ----------
            with gr.Tab("④ 自检与演示"):
                gr.Markdown("回归校验：用共识表1（药学服务 16 组合均分）重算 SHAP，应复现共识表2。")
                val_btn = gr.Button("运行回归校验")
                val_out = gr.Textbox(label="校验输出", lines=10)
                val_btn.click(ui_validate, [], val_out)
                demo_btn = gr.Button("运行合成数据演示（DEMO）")
                demo_out = gr.Textbox(label="演示输出", lines=18)
                demo_btn.click(ui_demo, [], demo_out)

            # ---------- ⑤ 已有结果 / 断点状态 ----------
            with gr.Tab("⑤ 已有结果（断点状态）"):
                gr.Markdown("**只读展示后台已落盘的数据**：每生成 1 条回答、每评完 1 条都已即时存盘，"
                            "此页每 10 秒自动刷新（也可点“手动刷新”），随时确认结果都在、关网页不丢。")
                res_refresh = gr.Button("🔄 手动刷新", variant="secondary")
                res_md = gr.Markdown()
                gr.Markdown("#### 各模型落盘进度")
                res_progress = gr.Dataframe(
                    headers=["模型", "生成回答", "单条评分", "一致性判断", "状态"],
                    interactive=False, wrap=True)
                gr.Markdown("#### 16 组合均分预览（已评条目折算分的 3 次均值；随评分推进逐步填满）")
                res_combo = gr.Dataframe(
                    headers=["提示词组合"] + [core.MODEL_DISPLAY[m] + " 均分" for m in core.MODEL_KEYS] +
                            ["三模型均分", "已评次数"],
                    interactive=False, wrap=True)
                gr.Markdown("#### 评分明细（每条已评分回答一行，9 指标 + 折算总分 + 一票否决）")
                res_detail = gr.Dataframe(
                    headers=["提示词组合", "被测模型", "生成序次"] +
                            [core.FIELD_CN[f] for f in core.IND_FIELDS] + ["折算总分", "一票否决"],
                    interactive=False, wrap=True)
                with gr.Accordion("📖 查看已落盘回答原文", open=False):
                    with gr.Row():
                        res_combo_dd = gr.Dropdown(choices=_combo_names(), value="H+A",
                                                   label="提示词组合")
                        res_model_dd = gr.Dropdown(
                            choices=[core.MODEL_DISPLAY[m] for m in core.MODEL_KEYS],
                            value=core.MODEL_DISPLAY["DeepSeek"], label="被测模型")
                    res_ans_btn = gr.Button("查看该单元已落盘回答")
                    res_ans_out = gr.Textbox(label="回答原文（含评分摘要）", lines=16)

                gr.Markdown("#### 📦 导出（全部评分明细 + 合成计算分析 + 原始数据）")
                with gr.Row():
                    exp_btn = gr.Button("导出 zip（xlsx 明细 10 表 + 分析摘要 + runstate/场景 JSON）")
                    exp_docx_btn = gr.Button("📄 附件导出（Word，六节框架）")
                exp_file = gr.File(label="导出包（点击下载）", interactive=False)
                exp_msg = gr.Markdown()

                _res_inputs = [scen_dd, el_p, el_h, el_a, el_r, el_m, el_e, el_materials,
                               gold_basic, gold_adv]
                exp_btn.click(ui_export_all, _res_inputs, [exp_file, exp_msg])
                exp_docx_btn.click(ui_export_docx, _res_inputs, [exp_file, exp_msg])
                res_outputs = [res_md, res_progress, res_combo, res_detail]
                res_refresh.click(ui_results_panel, _res_inputs, res_outputs)
                res_timer = gr.Timer(10)
                res_timer.tick(ui_results_panel, _res_inputs, res_outputs)
                res_ans_btn.click(ui_results_answer,
                                  [scen_dd, res_combo_dd, res_model_dd], res_ans_out)

        gr.Markdown("---\n报告输出目录：`outputs/`。详细用法见 `使用说明.md`。")
    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch(server_name="127.0.0.1", server_port=7860, inbrowser=True)
