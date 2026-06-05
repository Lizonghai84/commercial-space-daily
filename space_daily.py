#!/usr/bin/env python3
"""Generate a weighted commercial-space daily report from public APIs and RSS."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import difflib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


CATEGORIES = ("行业动态", "最新技术", "研究成果", "最新发射")
TECH_KEYWORDS = (
    "technology", "engine", "propulsion", "reusable", "landing", "manufacturing",
    "antenna", "laser", "optical", "inter-satellite", "in-orbit", "on-orbit",
    "technical", "prototype", "test flight", "试验", "技术", "发动机", "回收",
)
RESEARCH_KEYWORDS = (
    "study", "paper", "arxiv", "scientists", "researchers", "journal",
    "研究成果", "论文", "学者",
)
COMMERCIAL_KEYWORDS = (
    "commercial space", "space economy", "space industry", "launch", "rocket",
    "satellite", "constellation", "starlink", "spacesail", "qianfan", "guowang",
    "spaceport", "payload", "spacex", "blue origin", "rocket lab",
    "landspace", "galactic energy", "orienspace", "ispace", "relativity space",
    "firefly", "arianespace", "ula", "space force",
    "商业航天", "发射", "火箭", "卫星", "星座", "航天发射场",
)
TOKEN_RE = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


@dataclasses.dataclass
class Item:
    title: str
    url: str
    source: str
    published_at: dt.datetime
    summary: str
    category: str
    source_weight: int
    score: float = 0
    related_sources: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class SourceStatus:
    source: str
    ok: bool
    count: int = 0
    error: str = ""


def parse_datetime(value: str | None, default: dt.datetime | None = None) -> dt.datetime:
    if not value:
        if default is None:
            raise ValueError("missing datetime")
        return default
    value = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        from email.utils import parsedate_to_datetime

        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def clean_text(value: str | None, limit: int = 360) -> str:
    text = TAG_RE.sub(" ", html.unescape(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    for suffix in ("The post ", " appeared first on "):
        if suffix in text:
            text = text.split(suffix, 1)[0].strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def normalize_title(title: str) -> str:
    tokens = TOKEN_RE.findall(html.unescape(title).lower())
    noise = {"the", "a", "an", "to", "of", "for", "on", "in", "and", "with", "from"}
    return " ".join(token for token in tokens if token not in noise)


def similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, normalize_title(left), normalize_title(right)).ratio()


def keyword_hits(text: str, keywords: Iterable[str]) -> int:
    lowered = text.lower()
    return sum(1 for keyword in keywords if keyword.lower() in lowered)


def classify(title: str, summary: str, fixed: str | None = None) -> str:
    if fixed:
        return fixed
    text = f"{title} {summary}"
    if keyword_hits(text, RESEARCH_KEYWORDS) >= 1:
        return "研究成果"
    if keyword_hits(text, TECH_KEYWORDS) >= 1:
        return "最新技术"
    return "行业动态"


def is_relevant(title: str, summary: str) -> bool:
    return keyword_hits(f"{title} {summary}", COMMERCIAL_KEYWORDS) > 0


def request_bytes(url: str, params: dict[str, Any] | None = None, timeout: int = 30) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "commercial-space-daily/0.1 (+https://github.com/)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return json.loads(request_bytes(url, params).decode("utf-8"))


def fetch_snapi(config: dict[str, Any], now: dt.datetime, hours: int) -> tuple[list[Item], SourceStatus]:
    source = config["news_api"]
    try:
        payload = fetch_json(source["url"], {"limit": source["limit"]})
        cutoff = now - dt.timedelta(hours=hours)
        site_weights = config.get("news_site_weights", {})
        items = []
        for row in payload.get("results", []):
            published = parse_datetime(row.get("published_at"))
            if published < cutoff or published > now + dt.timedelta(hours=2):
                continue
            title = clean_text(row.get("title"), 240)
            summary = clean_text(row.get("summary"))
            if not is_relevant(title, summary):
                continue
            site = row.get("news_site") or source["name"]
            items.append(
                Item(
                    title=title,
                    url=row.get("url", ""),
                    source=site,
                    published_at=published,
                    summary=summary,
                    category=classify(title, summary),
                    source_weight=int(site_weights.get(site, source["weight"])),
                )
            )
        return items, SourceStatus(source["name"], True, len(items))
    except Exception as exc:
        return [], SourceStatus(source["name"], False, error=str(exc))


def child_text(element: ET.Element, names: Iterable[str]) -> str:
    for name in names:
        child = element.find(name)
        if child is not None and child.text:
            return child.text
    return ""


def fetch_rss(feed: dict[str, Any], now: dt.datetime, hours: int) -> tuple[list[Item], SourceStatus]:
    try:
        root = ET.fromstring(request_bytes(feed["url"]))
        entries = root.findall(".//item")
        if not entries:
            entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        cutoff = now - dt.timedelta(hours=hours)
        items = []
        for entry in entries:
            title = clean_text(child_text(entry, ("title", "{http://www.w3.org/2005/Atom}title")), 240)
            summary = clean_text(
                child_text(
                    entry,
                    (
                        "description",
                        "{http://purl.org/rss/1.0/modules/content/}encoded",
                        "{http://www.w3.org/2005/Atom}summary",
                        "{http://www.w3.org/2005/Atom}content",
                    ),
                )
            )
            published_value = child_text(
                entry,
                (
                    "pubDate",
                    "{http://purl.org/dc/elements/1.1/}date",
                    "{http://www.w3.org/2005/Atom}published",
                    "{http://www.w3.org/2005/Atom}updated",
                ),
            )
            published = parse_datetime(published_value, now)
            if published < cutoff or published > now + dt.timedelta(hours=2):
                continue
            link = child_text(entry, ("link",))
            if not link:
                link_element = entry.find("{http://www.w3.org/2005/Atom}link")
                link = link_element.attrib.get("href", "") if link_element is not None else ""
            if title and is_relevant(title, summary):
                items.append(
                    Item(
                        title=title,
                        url=link,
                        source=feed["name"],
                        published_at=published,
                        summary=summary,
                        category=classify(title, summary),
                        source_weight=int(feed["weight"]),
                    )
                )
        return items, SourceStatus(feed["name"], True, len(items))
    except Exception as exc:
        return [], SourceStatus(feed["name"], False, error=str(exc))


def launch_summary(row: dict[str, Any], upcoming: bool) -> str:
    status = (row.get("status") or {}).get("name", "状态待确认")
    pad = ((row.get("pad") or {}).get("location") or {}).get("name", "")
    mission = (row.get("mission") or {}).get("description", "")
    prefix = "计划发射" if upcoming else status
    parts = [prefix]
    if pad:
        parts.append(f"地点：{pad}")
    if mission:
        parts.append(clean_text(mission, 260))
    return "；".join(parts)


def fetch_launches(config: dict[str, Any], now: dt.datetime, hours: int) -> tuple[list[Item], list[SourceStatus]]:
    source = config["launch_api"]
    items: list[Item] = []
    statuses = []
    windows = (
        ("previous_url", False, now - dt.timedelta(hours=hours), now),
        ("upcoming_url", True, now, now + dt.timedelta(days=7)),
    )
    for key, upcoming, start, end in windows:
        label = f"{source['name']} ({'未来7天' if upcoming else '最近发射'})"
        try:
            payload = fetch_json(source[key], {"limit": source["limit"]})
            count = 0
            for row in payload.get("results", []):
                launch_time = parse_datetime(row.get("net"))
                if not start <= launch_time <= end:
                    continue
                items.append(
                    Item(
                        title=clean_text(row.get("name"), 240),
                        url=row.get("url", ""),
                        source=source["name"],
                        published_at=launch_time,
                        summary=launch_summary(row, upcoming),
                        category="最新发射",
                        source_weight=int(source["weight"]),
                    )
                )
                count += 1
            statuses.append(SourceStatus(label, True, count))
        except Exception as exc:
            statuses.append(SourceStatus(label, False, error=str(exc)))
    return items, statuses


def fetch_arxiv(config: dict[str, Any], now: dt.datetime) -> tuple[list[Item], SourceStatus]:
    source = config["research_api"]
    try:
        payload = request_bytes(
            source["url"],
            {
                "search_query": source["query"],
                "start": 0,
                "max_results": source["limit"],
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            },
            timeout=45,
        )
        root = ET.fromstring(payload)
        namespace = "{http://www.w3.org/2005/Atom}"
        cutoff = now - dt.timedelta(days=int(source["lookback_days"]))
        items = []
        for entry in root.findall(f"{namespace}entry"):
            title = clean_text(child_text(entry, (f"{namespace}title",)), 240)
            summary = clean_text(child_text(entry, (f"{namespace}summary",)))
            published = parse_datetime(child_text(entry, (f"{namespace}published",)))
            if published < cutoff or not is_relevant(title, summary):
                continue
            items.append(
                Item(
                    title=title,
                    url=child_text(entry, (f"{namespace}id",)),
                    source=source["name"],
                    published_at=published,
                    summary=summary,
                    category="研究成果",
                    source_weight=int(source["weight"]),
                )
            )
        return items, SourceStatus(source["name"], True, len(items))
    except Exception as exc:
        return [], SourceStatus(source["name"], False, error=str(exc))


def crossref_date(row: dict[str, Any]) -> dt.datetime:
    parts = ((row.get("published") or {}).get("date-parts") or [[1970]])[0]
    year = int(parts[0])
    month = int(parts[1]) if len(parts) > 1 else 1
    day = int(parts[2]) if len(parts) > 2 else 1
    return dt.datetime(year, month, day, tzinfo=dt.timezone.utc)


def fetch_crossref(config: dict[str, Any], now: dt.datetime) -> tuple[list[Item], SourceStatus]:
    source = config["crossref_api"]
    lookback_days = int(source["lookback_days"])
    start = (now - dt.timedelta(days=lookback_days)).date().isoformat()
    end = now.date().isoformat()
    try:
        payload = fetch_json(
            source["url"],
            {
                "query.bibliographic": source["query"],
                "filter": f"from-pub-date:{start},until-pub-date:{end}",
                "rows": source["limit"],
                "select": "DOI,title,abstract,published,URL,publisher",
            },
        )
        items = []
        for row in (payload.get("message") or {}).get("items", []):
            titles = row.get("title") or []
            title = clean_text(titles[0] if titles else "", 240)
            abstract = clean_text(row.get("abstract"))
            if not title or not is_relevant(title, abstract):
                continue
            publisher = clean_text(row.get("publisher"), 100)
            summary = abstract or f"发表于 {publisher or '学术出版物'}；DOI：{row.get('DOI', '未提供')}。"
            items.append(
                Item(
                    title=title,
                    url=row.get("URL", ""),
                    source=source["name"],
                    published_at=crossref_date(row),
                    summary=summary,
                    category="研究成果",
                    source_weight=int(source["weight"]),
                )
            )
        return items, SourceStatus(source["name"], True, len(items))
    except Exception as exc:
        return [], SourceStatus(source["name"], False, error=str(exc))


def rank_item(item: Item, now: dt.datetime) -> float:
    age_hours = abs((now - item.published_at).total_seconds()) / 3600
    relevance = keyword_hits(f"{item.title} {item.summary}", COMMERCIAL_KEYWORDS)
    freshness = max(0, 36 - min(age_hours, 36))
    return round(item.source_weight + min(relevance, 8) * 3 + freshness / 6, 2)


def deduplicate(items: list[Item], now: dt.datetime, threshold: float = 0.72) -> list[Item]:
    ranked = sorted(items, key=lambda item: rank_item(item, now), reverse=True)
    result: list[Item] = []
    for item in ranked:
        item.score = rank_item(item, now)
        duplicate = next(
            (
                existing
                for existing in result
                if item.category == existing.category
                and (item.url == existing.url or similarity(item.title, existing.title) >= threshold)
            ),
            None,
        )
        if duplicate:
            if item.source not in duplicate.related_sources and item.source != duplicate.source:
                duplicate.related_sources.append(item.source)
            continue
        result.append(item)
    return result


def selected_items(items: list[Item], per_category: int) -> list[Item]:
    return [
        item
        for category in CATEGORIES
        for item in [candidate for candidate in items if candidate.category == category][:per_category]
    ]


def contains_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def apply_deepseek_result(items: list[Item], result: dict[str, Any]) -> str:
    daily_overview = clean_text(result.get("daily_overview"), 500)
    if not daily_overview or not contains_chinese(daily_overview):
        raise ValueError("DeepSeek 未返回有效的中文今日速览")
    if len(daily_overview) > 260:
        raise ValueError(f"DeepSeek 今日速览过长：{len(daily_overview)} 字")
    summaries = {
        row.get("url"): clean_text(row.get("summary_zh"))
        for row in result.get("items", [])
        if isinstance(row, dict)
    }
    missing = [item.url for item in items if not summaries.get(item.url) or not contains_chinese(summaries[item.url])]
    if missing:
        raise ValueError(f"DeepSeek 缺少 {len(missing)} 条有效中文摘要")
    for item in items:
        item.summary = summaries[item.url]
    return daily_overview


def call_deepseek(messages: list[dict[str, str]], model: str, api_key: str) -> dict[str, Any]:
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "thinking": {"type": "disabled"},
                "max_tokens": 8192,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"].get("content")
    if not content:
        raise ValueError("DeepSeek 返回了空内容")
    return json.loads(content)


def enhance_with_deepseek(items: list[Item], model: str) -> tuple[str, SourceStatus]:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 DEEPSEEK_API_KEY，无法生成全中文日报")
    compact = [
        {
            "url": item.url,
            "title": item.title,
            "source": item.source,
            "category": item.category,
            "related_sources": item.related_sources,
            "summary": item.summary,
        }
        for item in items
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "你是严谨的商业航天日报中文编辑。只能使用输入中的事实，不得补充、猜测或夸大。"
                "所有输出必须使用简体中文，机构、公司、任务和型号名称可保留原文。"
            ),
        },
        {
            "role": "user",
            "content": (
                "根据输入内容输出 JSON 对象。为每一项写一条不超过100字的中文摘要，并基于全部内容"
                "写一段120至220字、2至3句的中文今日速览小结，只点出最重要的行业、技术、研究和发射趋势。"
                "必须为输入中的每个 URL 返回且只返回一项，不能遗漏。"
                'JSON 格式示例：{"daily_overview":"中文小结","items":[{"url":"原URL","summary_zh":"中文摘要"}]}。'
                "\n输入：" + json.dumps(compact, ensure_ascii=False)
            ),
        },
    ]
    last_error: Exception | None = None
    for _ in range(2):
        try:
            result = call_deepseek(messages, model, api_key)
            overview = apply_deepseek_result(items, result)
            return overview, SourceStatus(f"DeepSeek {model}", True, len(items))
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"DeepSeek 中文日报生成失败：{last_error}") from last_error


def render_report(
    items: list[Item],
    statuses: list[SourceStatus],
    report_date: dt.date,
    generated_at: dt.datetime,
    hours: int,
    per_category: int,
    daily_overview: str,
) -> str:
    counts = {category: 0 for category in CATEGORIES}
    for item in items:
        counts[item.category] += 1
    lines = [
        f"# 商业航天日报 | {report_date.isoformat()}",
        "",
        f"> 生成时间：{generated_at.strftime('%Y-%m-%d %H:%M %Z')}",
        f"> 新闻窗口：最近 {hours} 小时；发射预告：未来 7 天；研究成果：最近 14 天。",
        "> MVP 规则：按信源权重、商业航天关键词与时效性排序；标题相似度去重；DeepSeek V4 Flash 生成中文摘要与今日小结。",
        "",
        "## 今日速览",
        "",
        daily_overview,
        "",
        f"- 行业动态：{counts['行业动态']} 条候选",
        f"- 最新技术：{counts['最新技术']} 条候选",
        f"- 研究成果：{counts['研究成果']} 条候选",
        f"- 最新发射：{counts['最新发射']} 条候选",
        "",
    ]
    for category in CATEGORIES:
        lines.extend([f"## {category}", ""])
        selected = [item for item in selected_items(items, per_category) if item.category == category]
        if not selected:
            lines.extend(["_本次抓取窗口内暂无可用内容。_", ""])
            continue
        for item in selected:
            local_time = item.published_at.astimezone(generated_at.tzinfo)
            lines.extend(
                [
                    f"### [{item.title}]({item.url})",
                    "",
                    f"- **来源**：{item.source}（权重 {item.source_weight}，综合分 {item.score:.1f}）",
                    f"- **时间**：{local_time.strftime('%Y-%m-%d %H:%M %Z')}",
                    f"- **摘要**：{item.summary or '信源未提供摘要。'}",
                ]
            )
            if item.related_sources:
                lines.append(f"- **交叉信源**：{', '.join(item.related_sources)}")
            lines.append("")
    lines.extend(["## 信源运行状态", ""])
    for status in statuses:
        if status.ok:
            lines.append(f"- ✅ {status.source}：获取 {status.count} 条候选")
        else:
            lines.append(f"- ⚠️ {status.source}：失败（{clean_text(status.error, 180)}）")
    lines.extend(
        [
            "",
            "## 编辑说明",
            "",
            "- 本日报是自动生成的 MVP，重要事实应回到原始链接核验。",
            "- 中文官网、公众号、融资数据库与在轨核验尚未接入，后续按需求和稳定性迭代。",
            "",
        ]
    )
    return "\n".join(lines)


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def collect(config: dict[str, Any], now: dt.datetime, hours: int) -> tuple[list[Item], list[SourceStatus]]:
    all_items: list[Item] = []
    statuses: list[SourceStatus] = []
    items, status = fetch_snapi(config, now, hours)
    all_items.extend(items)
    statuses.append(status)
    for feed in config["rss"]:
        items, status = fetch_rss(feed, now, hours)
        all_items.extend(items)
        statuses.append(status)
    items, launch_statuses = fetch_launches(config, now, hours)
    all_items.extend(items)
    statuses.extend(launch_statuses)
    items, status = fetch_arxiv(config, now)
    all_items.extend(items)
    statuses.append(status)
    items, status = fetch_crossref(config, now)
    all_items.extend(items)
    statuses.append(status)
    return deduplicate(all_items, now), statuses


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/sources.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--date", help="日报日期，格式 YYYY-MM-DD；默认使用 Asia/Shanghai 今天")
    parser.add_argument("--hours", type=int, default=36, help="新闻回看窗口，默认 36 小时")
    parser.add_argument("--per-category", type=int, default=8, help="每个栏目最多输出条数")
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timezone = ZoneInfo("Asia/Shanghai")
    generated_at = dt.datetime.now(timezone)
    report_date = dt.date.fromisoformat(args.date) if args.date else generated_at.date()
    config = load_config(args.config)
    items, statuses = collect(config, generated_at.astimezone(dt.timezone.utc), args.hours)
    overview, status = enhance_with_deepseek(selected_items(items, args.per_category), args.model)
    statuses.append(status)
    report = render_report(items, statuses, report_date, generated_at, args.hours, args.per_category, overview)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{report_date.isoformat()}.md"
    output.write_text(report, encoding="utf-8")
    print(f"Generated {output} with {len(items)} deduplicated candidates")
    return 0 if any(status.ok and status.count for status in statuses) else 1


if __name__ == "__main__":
    sys.exit(main())
