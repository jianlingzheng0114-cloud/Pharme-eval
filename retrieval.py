# -*- coding: utf-8 -*-
"""
E 元素循证依据模块：附件解析 + 多源检索 + 标准化证据卡装配
============================================================
用途：为 PHARM-E 评测的 E 元素（示例/循证依据要素）提供证据材料。
  - 有附件（政策/指南/共识原文 pdf/docx/txt/md/html）→ 解析原文作为最高效力证据；
  - 无附件 → 保留系统自身检索能力，自动检索 E 元素引用的文献/政策。

设计借鉴 nature-academic-search / nature-literature-pipeline 科研技能：
  1. T1→T2→T3 来源可靠性分级与回退路由（来源独立容错，一源失败不影响其余）
  2. 免密钥公共 API 优先：PubMed E-utilities(T1)、CrossRef(T1)、OpenAlex(T1/发现)、
     Semantic Scholar(T2)；政策网页检索为 T3（结果可能不完整，须人工复核）
  3. 结果去重（规范化标题）、无结果时自动放宽查询（查询扩展）
  4. 证据卡标准化：标题/来源/日期/标识(DOI/PMID/URL)/访问日期/原文摘录——可溯源可核查
  5. 证据冻结：检索结果连同检索式、时间戳存档 json，保证 16 组合证据一致、可复现

命令行用法：
  python retrieval.py --query "《医疗机构药事管理规定》" --sources pubmed,crossref,openalex,policy
  python retrieval.py --file 附件.docx --name 药事管理
"""
import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
ACCESS_DATE = datetime.now().strftime("%Y-%m-%d")

DEFAULT_SEARCH_CFG = {
    "mailto": "",                  # OpenAlex/CrossRef 礼貌池联系方式（可选）
    "pubmed_api_key": "",          # NCBI API key（可选，3→10 req/s）
    "semantic_scholar_api_key": "",  # 可选，1→100 req/s
    "per_source_limit": 5,         # 每源每查询返回条数
    "timeout": 20,
    "max_evidence_chars": 20000,   # 单份附件证据正文截断上限
}

SOURCE_ORDER = ["pubmed", "crossref", "openalex", "semantic_scholar", "policy"]
SOURCE_LABEL = {
    "pubmed": "PubMed（T1，生物医学文献）",
    "crossref": "CrossRef（T1，跨学科学术文献）",
    "openalex": "OpenAlex（T1，综合文献发现）",
    "semantic_scholar": "Semantic Scholar（T2，引文网络）",
    "policy": "政策网页检索（T3，可能不完整，须人工复核）",
}


# ---------------- 通用 HTTP ----------------
def _http_get(url, timeout=20, headers=None):
    hdr = {"User-Agent": "PHARM-E-Evidence/1.0 (research evaluation tool)",
           "Accept": "application/json, text/html, */*"}
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_json(url, timeout=20, headers=None):
    return json.loads(_http_get(url, timeout, headers).decode("utf-8", "ignore"))


def _strip_tags(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


# ---------------- 附件解析 ----------------
def extract_file_text(path):
    """解析附件原文文本。支持 pdf / docx / txt / md / html；旧版 doc 提示转换。"""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    if ext in ("txt", "md", "markdown"):
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "ignore")
    if ext == "docx":
        import zipfile
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return _strip_tags(xml).replace("\n ", "\n")
    if ext in ("html", "htm"):
        raw = open(path, "rb").read().decode("utf-8", "ignore")
        raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
        return _strip_tags(raw)
    if ext == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise RuntimeError("解析 PDF 需要 pypdf：pip install pypdf")
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if ext == "doc":
        raise ValueError("旧版 .doc 不支持直接解析，请先另存为 .docx 或 .txt")
    raise ValueError(f"不支持的附件格式：.{ext}（支持 pdf/docx/txt/md/html）")


def extract_cited_titles(text):
    """从 E 元素文本提取《》内引用的法规/指南/共识/文献名，作默认检索式。"""
    seen, out = set(), []
    for t in re.findall(r"《([^》]{2,80})》", text or ""):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ---------------- 检索源（T1/T2/T3） ----------------
def _entry(title, abstract, source, date_str, url, ids=None, type_hint="文献"):
    return {"title": _strip_tags(title), "abstract": _strip_tags(abstract)[:1200],
            "source": source, "date": date_str or "", "url": url or "",
            "ids": ids or {}, "type_hint": type_hint, "access_date": ACCESS_DATE}


def search_pubmed(query, limit=5, api_key=None, timeout=20):
    """T1：PubMed E-utilities（esearch + efetch XML，stdlib 解析）"""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    q = {"db": "pubmed", "term": query, "retmax": limit, "retmode": "json",
         "tool": "pharme_eval"}
    if api_key:
        q["api_key"] = api_key
    data = _http_json(f"{base}/esearch.fcgi?{urllib.parse.urlencode(q)}", timeout)
    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    q2 = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "tool": "pharme_eval"}
    if api_key:
        q2["api_key"] = api_key
    xml = _http_get(f"{base}/efetch.fcgi?{urllib.parse.urlencode(q2)}", timeout).decode("utf-8", "ignore")
    out = []
    for art in re.findall(r"(?s)<PubmedArticle>(.*?)</PubmedArticle>", xml):
        pmid = re.search(r"(?s)<PMID[^>]*>(\d+)</PMID>", art)
        title = re.search(r"(?s)<ArticleTitle>(.*?)</ArticleTitle>", art)
        abstract = " ".join(_strip_tags(m) for m in
                            re.findall(r"(?s)<AbstractText[^>]*>(.*?)</AbstractText>", art))
        year = re.search(r"(?s)<PubDate>.*?<Year>(\d{4})</Year>", art) or \
            re.search(r"(?s)<ArticleDate>.*?<Year>(\d{4})</Year>", art)
        journal = re.search(r"(?s)<Title>(.*?)</Title>", art)
        doi = re.search(r'(?s)<ELocationID EIdType="doi"[^>]*>(.*?)</ELocationID>', art)
        pmid_s = pmid.group(1) if pmid else ""
        out.append(_entry(title.group(1) if title else "", abstract,
                          f"PubMed / {journal.group(1) if journal else ''}".strip(" /"),
                          year.group(1) if year else "",
                          f"https://pubmed.ncbi.nlm.nih.gov/{pmid_s}/" if pmid_s else "",
                          {"pmid": pmid_s, "doi": doi.group(1) if doi else ""}))
    return out


def search_crossref(query, limit=5, mailto=None, timeout=20):
    """T1：CrossRef REST API"""
    q = {"query.bibliographic": query, "rows": limit}
    if mailto:
        q["mailto"] = mailto
    data = _http_json(f"https://api.crossref.org/works?{urllib.parse.urlencode(q)}", timeout)
    out = []
    for it in data.get("message", {}).get("items", []):
        title = (it.get("title") or [""])[0]
        year = ""
        parts = (it.get("issued") or {}).get("date-parts") or [[]]
        if parts and parts[0]:
            year = str(parts[0][0])
        journal = (it.get("container-title") or [""])[0]
        out.append(_entry(title, it.get("abstract", ""), f"CrossRef / {journal}".strip(" /"),
                          year, it.get("URL", ""), {"doi": it.get("DOI", "")}))
    return out


def search_openalex(query, limit=5, mailto=None, timeout=20):
    """T1：OpenAlex（免费无密钥，覆盖 CrossRef/PubMed/arXiv 沉积文献）"""
    q = {"search": query, "per_page": limit}
    if mailto:
        q["mailto"] = mailto
    data = _http_json(f"https://api.openalex.org/works?{urllib.parse.urlencode(q)}", timeout)
    out = []
    for it in data.get("results", []):
        inv = it.get("abstract_inverted_index") or {}
        abstract = ""
        if inv:
            pos = {}
            for word, idxs in inv.items():
                for i in idxs:
                    pos[i] = word
            abstract = " ".join(pos[i] for i in sorted(pos))
        doi = (it.get("doi") or "").replace("https://doi.org/", "")
        venue = ((it.get("primary_location") or {}).get("source") or {}).get("display_name", "")
        out.append(_entry(it.get("display_name", ""), abstract,
                          f"OpenAlex / {venue}".strip(" /"),
                          str(it.get("publication_year") or ""),
                          it.get("doi") or it.get("id", ""), {"doi": doi}))
    return out


def search_semantic_scholar(query, limit=5, api_key=None, timeout=20):
    """T2：Semantic Scholar Graph API"""
    q = {"query": query, "limit": limit,
         "fields": "title,abstract,year,venue,externalIds,url"}
    headers = {"x-api-key": api_key} if api_key else None
    data = _http_json(
        f"https://api.semanticscholar.org/graph/v1/paper/search?{urllib.parse.urlencode(q)}",
        timeout, headers)
    out = []
    for it in data.get("data", []):
        ext = it.get("externalIds") or {}
        out.append(_entry(it.get("title", ""), it.get("abstract") or "",
                          f"Semantic Scholar / {it.get('venue') or ''}".strip(" /"),
                          str(it.get("year") or ""), it.get("url") or "",
                          {"doi": ext.get("DOI", ""), "pmid": str(ext.get("PubMed", "") or "")}))
    return out


def search_policy_web(query, limit=5, timeout=20):
    """T3：政策/指南网页检索（Bing RSS，免密钥）。结果可能不完整或过时，须人工复核。
    自动定向政府与学协会站点（gov.cn / nhc / nmpa / cn-healthcare 学会站点）。"""
    site_filter = "(site:gov.cn OR site:nhc.gov.cn OR site:nmpa.gov.cn OR site:cma.org.cn)"
    full_query = f"{query} {site_filter}"
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": full_query, "format": "rss", "count": limit})
    raw = _http_get(url, timeout).decode("utf-8", "ignore")
    out = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", raw)[:limit]:
        t = re.search(r"(?s)<title>(.*?)</title>", item)
        l = re.search(r"(?s)<link>(.*?)</link>", item)
        d = re.search(r"(?s)<description>(.*?)</description>", item)
        p = re.search(r"(?s)<pubDate>(.*?)</pubDate>", item)
        out.append(_entry(t.group(1) if t else "", d.group(1) if d else "",
                          "政策网页检索（Bing，T3）",
                          p.group(1)[:16] if p else "", l.group(1) if l else "",
                          {}, "政策/网页"))
    return out


SEARCH_FUNCS = {
    "pubmed": lambda q, cfg: search_pubmed(q, cfg["per_source_limit"], cfg.get("pubmed_api_key"), cfg["timeout"]),
    "crossref": lambda q, cfg: search_crossref(q, cfg["per_source_limit"], cfg.get("mailto"), cfg["timeout"]),
    "openalex": lambda q, cfg: search_openalex(q, cfg["per_source_limit"], cfg.get("mailto"), cfg["timeout"]),
    "semantic_scholar": lambda q, cfg: search_semantic_scholar(q, cfg["per_source_limit"],
                                                              cfg.get("semantic_scholar_api_key"), cfg["timeout"]),
    "policy": lambda q, cfg: search_policy_web(q, cfg["per_source_limit"], cfg["timeout"]),
}


# ---------------- 路由、去重与证据装配 ----------------
def _norm_title(t):
    return re.sub(r"[\s\W]+", "", (t or "").lower())


def dedup_entries(entries):
    seen, out = set(), []
    for e in entries:
        key = _norm_title(e["title"])[:60]
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def expand_queries(queries):
    """查询扩展：无结果兜底用（去掉书名号/限定词，词组转空格）"""
    out = []
    for q in queries:
        out.append(q)
        stripped = re.sub(r"[《》\"'（）()【】]", " ", q).strip()
        if stripped and stripped != q:
            out.append(stripped)
    return out


def run_retrieval(queries, sources, cfg=None):
    """按 T1→T2→T3 路由执行多源检索。每源独立容错；某查询无结果时自动扩展查询重试。
    返回 (entries, errors)。"""
    cfg = {**DEFAULT_SEARCH_CFG, **(cfg or {})}
    sources = [s for s in (sources or SOURCE_ORDER) if s in SEARCH_FUNCS]
    all_entries, errors = [], []
    for q in queries:
        q_entries = []
        for s in sources:
            try:
                got = SEARCH_FUNCS[s](q, cfg)
                q_entries += got
                if not got:
                    errors.append(f"[{SOURCE_LABEL[s]}] 查询“{q}”无结果")
            except Exception as ex:
                errors.append(f"[{SOURCE_LABEL[s]}] 查询“{q}”失败：{type(ex).__name__}: {str(ex)[:120]}")
            time.sleep(0.5)  # 礼貌限速
        if not q_entries:  # 查询扩展后重试第一可用源
            for q2 in expand_queries([q])[1:]:
                for s in sources:
                    try:
                        got = SEARCH_FUNCS[s](q2, cfg)
                        if got:
                            q_entries = got
                            errors.append(f"[查询扩展] “{q}”无结果，改用“{q2}”命中 {len(got)} 条")
                            break
                    except Exception:
                        continue
                if q_entries:
                    break
        all_entries += q_entries
    return dedup_entries(all_entries), errors


# ---------------- 证据卡装配 ----------------
def cards_from_files(paths, max_chars=None):
    """附件 → 证据卡（法规/指南/共识原文，最高效力）。"""
    max_chars = max_chars or DEFAULT_SEARCH_CFG["max_evidence_chars"]
    cards, errors = [], []
    for p in paths or []:
        try:
            text = extract_file_text(p).strip()
            if not text:
                errors.append(f"[附件] {os.path.basename(p)} 解析为空")
                continue
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n…（原文超 {max_chars} 字已截断，完整原文见附件文件）"
            cards.append({"title": os.path.basename(p), "abstract": text,
                          "source": "用户上传附件原文（最高效力证据）", "date": "",
                          "url": "", "ids": {}, "type_hint": "附件原文",
                          "access_date": ACCESS_DATE})
        except Exception as ex:
            errors.append(f"[附件] {os.path.basename(p)} 解析失败：{type(ex).__name__}: {str(ex)[:120]}")
    return cards, errors


def build_evidence_text(cards, mode_label):
    """标准化证据卡文本（进入 materials，16 组合保持一致）。"""
    lines = [f"【E 元素循证依据（{mode_label}，生成日期 {ACCESS_DATE}）】"]
    for i, c in enumerate(cards, 1):
        ids = "；".join(f"{k.upper()}={v}" for k, v in (c.get("ids") or {}).items() if v)
        lines.append(f"证据{i}｜{c['title']}")
        lines.append(f"  类型：{c.get('type_hint', '文献')}｜来源：{c.get('source', '')}"
                     f"{'｜日期：' + c['date'] if c.get('date') else ''}")
        ident = "；".join(x for x in [ids, ("URL=" + c["url"]) if c.get("url") else ""] if x)
        lines.append(f"  标识：{ident or '（附件原文，无外部标识）'}"
                     f"｜访问/核验日期：{c.get('access_date', ACCESS_DATE)}")
        lines.append(f"  原文摘录：{c.get('abstract', '')}")
        lines.append("")
    lines.append("【证据使用规则】以上证据在全部 16 种提示词组合中保持一致；"
                 "引用证据须注明具体条款/出处，禁止虚构法规条文、指南条款或数据。")
    return "\n".join(lines)


def merge_evidence_block(materials, evidence_block):
    """把证据块并入 materials；重复生成时替换旧证据块。"""
    materials = materials or ""
    marker = "【E 元素循证依据"
    if marker in materials:
        materials = materials.split(marker)[0].rstrip()
    materials = materials.strip()
    return (materials + "\n---\n" + evidence_block).strip() if materials else evidence_block


def save_evidence(out_dir, scenario_name, mode_label, queries, cards, errors):
    """证据冻结存档：检索式、结果、错误、时间戳全部落盘，保证可复现。"""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(out_dir, f"{scenario_name}_evidence_{stamp}.json")
    json.dump({"scenario": scenario_name, "mode": mode_label, "queries": queries,
               "generated_at": stamp, "access_date": ACCESS_DATE,
               "cards": cards, "errors": errors},
              open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return path


# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser(description="E 元素循证依据：附件解析 + 多源检索")
    ap.add_argument("--file", nargs="*", help="附件路径（pdf/docx/txt/md/html）")
    ap.add_argument("--query", nargs="*", help="检索式（可多条）")
    ap.add_argument("--sources", default=",".join(SOURCE_ORDER),
                    help=f"检索源，逗号分隔：{','.join(SOURCE_ORDER)}")
    ap.add_argument("--name", default="E证据")
    ap.add_argument("--out", default=os.path.join(TOOL_DIR, "outputs"))
    ap.add_argument("--config", default=os.path.join(TOOL_DIR, "config.json"))
    args = ap.parse_args()

    cfg = dict(DEFAULT_SEARCH_CFG)
    if os.path.exists(args.config):
        try:
            cfg.update(json.load(open(args.config, encoding="utf-8")).get("search", {}))
        except Exception:
            pass

    cards, errors = [], []
    if args.file:
        c, e = cards_from_files(args.file)
        cards += c
        errors += e
    if args.query:
        c, e = run_retrieval(args.query, args.sources.split(","), cfg)
        cards += c
        errors += e

    if not cards:
        print("未获得任何证据。", file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(1)

    mode = "附件解析" if args.file and not args.query else ("自动检索" if args.query and not args.file else "附件+检索")
    text = build_evidence_text(cards, mode)
    ev_path = save_evidence(args.out, args.name, mode, args.query or [], cards, errors)
    print(text)
    print(f"\n[存档] {ev_path}", file=sys.stderr)
    for e in errors:
        print(f"[提示] {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
