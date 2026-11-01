# PHARM-E 提示词要素测评工具 | PHARM-E Evaluation Tool

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

基于 **PHARM-E 六要素提示词框架**的提示词要素贡献度评估工具：通过 16 种要素组合 × 多模型 × 多次重复的受控实验，用 **Shapley 值分解**量化每个提示词要素对回答质量的边际贡献，并给出 bootstrap 95% 置信区间与效率性校验。本地 Gradio 界面，开箱即用。

An evaluation tool for measuring the marginal contribution of individual prompt elements in the **PHARM-E six-element prompt framework** (P/H/A/R/M/E). It runs controlled experiments over 16 element combinations × multiple LLMs × multiple repetitions, then decomposes each element's contribution to answer quality via **Shapley values**, with bootstrap 95% confidence intervals and an efficiency check. Runs locally with Gradio — no server deployment required.

---

# 中文文档

## 功能特性

- **六要素提示词实验**：P（任务目的）/ H（场景描述）/ A（任务摘要）/ R（角色定位）/ M（方法路径）/ E（循证依据与伦理准则）；H+A 固定为基座，P/R/M/E 全子集共 16 种组合
- **多模型对照测评**：每个被测模型对同一场景生成 16 组合 × 3 次回答，由独立裁判模型按金标准 8 指标评分 + 一票否决 + 组层一致性判断
- **Shapley 值归因**：144 条单次评分 → 48 个模型层效用 → 16 个联盟效用，计算 4 个可变要素的 Shapley 值、bootstrap 95%CI（1000 次重抽样）、效率性校验（Σφ ≟ v(全联盟)−v(空联盟)）
- **断点续跑**：逐条落盘，误关网页/断电/限流中断后自动续跑；指纹校验防止新旧数据混用
- **API 限流自愈**：对 HTTP 429/服务繁忙指数退避重试（8→16→32→60 秒），错峰可跑完
- **纯计算模式**：已有评分数据可直接导入计算（无需调用 API），仓库附带 xlsx 模板
- **一键导出**：全量评分明细 zip（10 张表 + 分析摘要 + 原始数据）、Word 附件（六节框架）

## 方法学摘要

| 环节 | 设计 |
|---|---|
| 实验单元 | 16 组合 × 3 模型 × 3 次重复 = 144 条单次回答 |
| 评分 | 裁判模型对照金标准评 8 项指标（含危害严重性），折算总分 0–100；严重危害一票否决计 0 |
| 一致性 | 同组合 3 次回答的语义相似度（4 点量表）作组层赋分，计入效用 |
| 归因 | Shapley 值（4 个可变要素）+ bootstrap 95%CI + 效率性校验 |
| 技术失败 | API/网络失败重试但**不计 0 分**（不污染效用估计） |

## 安装

要求 Python 3.10+。

```bash
git clone <本仓库>
cd PHARM-E
pip install -r requirements.txt
```

配置 API（任选一种 OpenAI 兼容端点，如硅基流动、阿里云百炼等）：

```bash
# Windows
copy config.example.json config.json
# Linux / macOS
cp config.example.json config.json
```

编辑 `config.json`，填入你的 `base_url`、`api_key`、裁判模型与被测模型 ID。**config.json 含密钥，已在 .gitignore 中排除，切勿提交。**

## 快速开始

```bash
python app.py
# 或 Windows 双击 启动PHARM-E评价工具.bat
```

浏览器自动打开 `http://127.0.0.1:7860`，界面流程：

1. **① 全自动评测**：选场景 → 核对/填写六要素 + 固定任务材料 + 金标准 → 一键跑完三模型
2. **分模型按钮**（DeepSeek / Qwen / Kimi）：可并行、各有独立日志框、各自断点续跑
3. **② 已有回答 → 评分** / **③ 纯计算**：不调 API，导入已有评分数据计算
4. **合成**：三模型完成后计算 Shapley 并出报告
5. **导出**：zip 全量明细 / Word 附件

`scenarios/` 内置五个**空白**场景模板（药学服务 / 药学教育 / 药学科研 / 药事管理 / 药学科普，仅含空槽位与默认一票否决规则，不含任何内容）：在 ① 页填好六要素、材料与金标准后点"💾 保存当前场景内容"即可写入并复用。

## 纯计算模式的数据格式

第 1 列为 16 个提示词组合名（模板已预置），数值列二选一：

- **3 次重复格式**（推荐）：9 列 `DeepSeek_1..3 / Qwen_1..3 / Kimi_1..3`，可算 bootstrap 95%CI
- **均分格式**：3 列 `DeepSeek / Qwen / Kimi` 均分（CI 不适用）

数值 = 折算总分 0–100，一票否决计 0。xlsx 只读第一个工作表，也支持 csv。仓库已附带 4 个 xlsx（3 次重复模板 / 均分模板 / 填写说明 / 合成数据示例）；`tools/make_compute_templates.py` 可重新生成；`tools/export_compute_table.py` 可从测评断点直接导出纯计算导入表。

## 目录结构

```
PHARM-E/
├── app.py                  # Gradio 界面与运行编排
├── pharme_eval.py          # 核心流水线：生成/评分/聚合/Shapley/导出
├── retrieval.py            # E 元素循证依据多源检索（PubMed/CrossRef/OpenAlex 等）
├── config.example.json     # 配置模板（复制为 config.json 后填写）
├── 启动PHARM-E评价工具.bat  # Windows 一键启动
├── 使用说明.md             # 详细使用手册（含排障）
├── 纯计算模板_*.xlsx       # 纯计算模式模板与合成示例
├── scenarios/              # 五个空白场景模板（内容由用户自行填写保存）
├── tools/                  # 纯计算模板生成、断点→纯计算表导出
└── tests/                  # 回归测试（全部 mock API，不发真实请求）
```

## 测试

```bash
python tests/test_export.py        # 导出功能（zip + Word）
python tests/test_model_split.py   # 分模型测评 + 合成
python tests/test_resume_button.py # 按钮级断点续跑
python tests/test_resume_gen.py    # 生成级断点续跑
```

测试全部使用 mock API，不产生网络调用，可离线运行。

## 常见问题

- **HTTP 429 / "System is too busy"**：服务端限流（高峰容量不足），不是密钥问题。工具会指数退避自动重试；仍失败就错峰（中午/晚间）再跑，断点会续。
- **提示"已中止，历史数据未被覆盖"**：本次的要素/材料与断点指纹不一致（常见于重启后材料框没恢复原内容）。恢复相同内容续跑，或勾选"强制重新开始"全部重来。
- **点按钮"没反应"**：查看 `outputs/运行日志.txt`，最后一条日志就是断点位置；浏览器断开会导致任务被 Gradio 静默取消，重新点击会自动续跑。

## 参考文献

PHARM-E 提示词框架源自 2025 年医院药学生成式 AI 多学科专家共识，发表于《医药导报》2026 年第 45 卷第 4 期（549–566 页）。

## 免责声明

- 本工具输出的评分与归因结果依赖裁判模型与金标准质量，仅供科研与提示词工程参考，不构成临床决策依据。
- 使用第三方大模型 API 产生的费用与配额消耗由使用者自行承担。
- 场景与金标准内容由使用者自行提供，其质量直接影响评分结果。

## 作者

[待填写：姓名 / 专业方向 / 单位]

---

# English Documentation

## Key Features

- **Six-element experiments**: P (Purpose) / H (Hospital context) / A (Assignment) / R (Role) / M (Method) / E (Evidence & Ethics). H + A form the fixed base; the power set of P/R/M/E yields 16 combinations.
- **Multi-model comparison**: each candidate model answers every combination × 3 repetitions; an independent judge model scores answers against a gold standard (8 criteria, converted to a 0–100 total, with veto for severe harm) plus a per-combination consistency assessment.
- **Shapley attribution**: 144 single scores → 48 model-level utilities → 16 coalition utilities → Shapley values for the 4 variable elements, bootstrap 95% CI (1000 resamples), and an efficiency check (Σφ ≟ v(full) − v(empty)).
- **Resume support**: results are persisted after every item; an interrupted run (browser closed, power loss, rate limits) resumes from the checkpoint. A fingerprint check prevents mixing incompatible experiments.
- **Rate-limit self-healing**: exponential backoff for HTTP 429 / "service too busy" (8→16→32→60 s).
- **Compute-only mode**: import pre-computed scores and get results without calling any API; xlsx templates included.
- **One-click export**: full score details as zip (10 sheets + summary + raw data) and a Word attachment (six-section framework).

## Methodology at a Glance

| Aspect | Design |
|---|---|
| Experimental unit | 16 combos × 3 models × 3 repetitions = 144 single runs |
| Scoring | Judge model scores 8 criteria against the gold standard (incl. harm severity), converted to a 0–100 total; severe harm triggers a veto (score 0) |
| Consistency | Semantic similarity of the 3 answers within a combination (4-point scale) contributes a group-level score to the utility |
| Attribution | Shapley values (4 variable elements) + bootstrap 95% CI + efficiency check |
| Technical failures | API/network errors are retried and never scored as 0 (no pollution of utility estimates) |

## Installation

Requires Python 3.10+.

```bash
git clone <this repository>
cd PHARM-E
pip install -r requirements.txt
cp config.example.json config.json   # Windows: copy config.example.json config.json
```

Edit `config.json`: set your `base_url`, `api_key`, judge model and candidate model IDs. Any OpenAI-compatible endpoint works (e.g., SiliconFlow, Alibaba Bailian). **`config.json` contains your API key, is excluded via `.gitignore`, and must never be committed.**

## Quick Start

```bash
python app.py
# Windows: double-click 启动PHARM-E评价工具.bat
```

The browser opens `http://127.0.0.1:7860`. Workflow:

1. **① Full auto evaluation**: pick a scenario → fill in the six elements + fixed task materials + gold standard → run all three models in one click
2. **Per-model buttons** (DeepSeek / Qwen / Kimi): run in parallel, each with its own log pane and checkpoint
3. **② Score existing answers** / **③ Compute only**: no API calls; import existing scores
4. **Synthesis**: compute Shapley values and the report once all three models finish
5. **Export**: zip of full details / Word attachment

`scenarios/` ships five **blank** scenario templates (pharmaceutical care / education / research / administration / public education — empty slots and default veto rule only, no content). Fill in the elements, materials and gold standard on page ①, then click "💾 保存当前场景内容" (Save current scenario) to persist and reuse.

## Compute-Only Mode

Column 1 holds the 16 prompt-combination names (pre-filled in the templates). Numeric columns, one of:

- **3-repetition format** (recommended): 9 columns `DeepSeek_1..3 / Qwen_1..3 / Kimi_1..3` — enables bootstrap 95% CI
- **Mean format**: 3 columns `DeepSeek / Qwen / Kimi` (no CI)

Values are converted totals (0–100); veto = 0. Only the first worksheet is read; csv is also supported. Four xlsx files are included (3-repetition template / mean template / field guide / synthetic example); regenerate with `tools/make_compute_templates.py`; `tools/export_compute_table.py` exports an import table directly from evaluation checkpoints.

## Repository Layout

```
PHARM-E/
├── app.py                  # Gradio UI and orchestration
├── pharme_eval.py          # Core pipeline: generation / judging / aggregation / Shapley / export
├── retrieval.py            # Multi-source evidence retrieval for element E (PubMed/CrossRef/OpenAlex, etc.)
├── config.example.json     # Config template (copy to config.json and fill in)
├── 启动PHARM-E评价工具.bat  # One-click Windows launcher
├── 使用说明.md             # Full user manual (incl. troubleshooting)
├── 纯计算模板_*.xlsx       # Templates and synthetic example for compute-only mode
├── scenarios/              # Five blank scenario templates (content filled by the user)
├── tools/                  # Template generation, checkpoint → compute-table export
└── tests/                  # Regression tests (all mock API, no real requests)
```

## Tests

```bash
python tests/test_export.py        # Export (zip + Word)
python tests/test_model_split.py   # Per-model evaluation + synthesis
python tests/test_resume_button.py # Button-level checkpoint resume
python tests/test_resume_gen.py    # Generation-level checkpoint resume
```

All tests use a mock API and run fully offline.

## FAQ

- **HTTP 429 / "System is too busy"**: server-side rate limiting (peak capacity), not an API-key problem. The tool retries with exponential backoff; if it still fails, run off-peak (noon/evening) — checkpoints will resume.
- **"Aborted, historical data preserved" warning**: the elements/materials you entered no longer match the checkpoint fingerprint (usually the materials box was not restored after a restart). Restore the identical content and continue, or tick "Force restart" to start over.
- **Button "does nothing"**: check `outputs/运行日志.txt` — the last log line is the checkpoint position. A dropped browser connection silently cancels the Gradio task; clicking again resumes.

## Citation

The PHARM-E prompt framework originates from the 2025 multidisciplinary expert consensus on generative AI in hospital pharmacy, published in *Journal of Medical Bulletin* (《医药导报》), 2026, 45(4): 549–566.

## Disclaimer

- Scores and attribution results depend on the judge model and the quality of your gold standard. For research and prompt-engineering reference only — not a basis for clinical decisions.
- API costs and quota consumption of third-party LLM services are borne by the user.
- Scenario and gold-standard content is supplied by the user; its quality directly affects the results.

## Author

[To be filled: Name / Specialization / Affiliation]

## License

MIT — see [LICENSE](LICENSE).
