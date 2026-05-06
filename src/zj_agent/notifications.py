from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.engine import Engine

from .config import Settings
from .db import fetch_clue_pool, mark_clue_pool_pushed
from .notify import FeishuNotifier

MAX_NOTIFY_AGE_DAYS = 30
PREFERRED_NOTIFY_AGE_DAYS = 7
SEARCH_SOURCE_PREFIXES = ("zj_search",)

CLUE_TYPE_LABELS = {
    "transfer": "成果转化",
    "achievement": "科研成果",
    "research": "研究进展",
    "award": "奖项成果",
    "patent": "专利成果",
    "prototype": "样机/中试",
    "other": "其他",
    "noise": "噪声",
}


@dataclass
class NotifyStats:
    scanned_docs: int = 0
    sent_docs: int = 0
    sent_messages: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "scanned_docs": self.scanned_docs,
            "sent_docs": self.sent_docs,
            "sent_messages": self.sent_messages,
        }


def _build_feishu_notifier(settings: Settings) -> FeishuNotifier:
    return FeishuNotifier(
        webhook_url=settings.feishu_webhook_url,
        sign_secret=settings.feishu_sign_secret,
        timeout_seconds=max(10, settings.http_timeout_seconds),
    )


def _format_published_at(value: datetime | None) -> str:
    if value is None:
        return "未知"
    return value.strftime("%Y-%m-%d")


def _type_label(value: str | None) -> str:
    key = (value or "").strip().lower()
    if not key:
        return "未知"
    return CLUE_TYPE_LABELS.get(key, value or "未知")


def _truncate(text: str | None, limit: int) -> str:
    value = (text or "").strip()
    if not value:
        return "暂无"
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _days_since(published_at: datetime | None, *, now: datetime) -> int | None:
    if published_at is None:
        return None
    return max(0, (now.date() - published_at.date()).days)


def _sort_rows(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            row.get("published_at") or datetime.min,
            int(row.get("relevance_score") or 0),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )


def _is_search_source(row: dict) -> bool:
    source_key = str(row.get("source_key") or "").strip().lower()
    return any(source_key.startswith(prefix) for prefix in SEARCH_SOURCE_PREFIXES)


def _take_one_per_source(rows: list[dict], *, chosen_ids: set[int], limit: int) -> list[dict]:
    picked: list[dict] = []
    seen_sources: set[str] = set()
    for row in _sort_rows(rows):
        row_id = int(row["id"])
        source_name = str(row.get("source_name") or "未知来源")
        if row_id in chosen_ids or source_name in seen_sources:
            continue
        picked.append(row)
        chosen_ids.add(row_id)
        seen_sources.add(source_name)
        if len(picked) >= limit:
            break
    return picked


def _round_robin_fill(rows: list[dict], *, chosen_ids: set[int], remaining: int) -> list[dict]:
    if remaining <= 0:
        return []
    grouped: dict[str, list[dict]] = {}
    for row in _sort_rows(rows):
        row_id = int(row["id"])
        if row_id in chosen_ids:
            continue
        source_name = str(row.get("source_name") or "未知来源")
        grouped.setdefault(source_name, []).append(row)
    ordered_sources = sorted(
        grouped,
        key=lambda source_name: grouped[source_name][0].get("published_at") or datetime.min,
        reverse=True,
    )
    picked: list[dict] = []
    while len(picked) < remaining:
        progressed = False
        for source_name in ordered_sources:
            bucket = grouped[source_name]
            if not bucket:
                continue
            row = bucket.pop(0)
            row_id = int(row["id"])
            if row_id in chosen_ids:
                continue
            picked.append(row)
            chosen_ids.add(row_id)
            progressed = True
            if len(picked) >= remaining:
                break
        if not progressed:
            break
    return picked


def _select_from_bucket(rows: list[dict], *, limit: int, chosen_ids: set[int]) -> list[dict]:
    selected: list[dict] = []
    selected.extend(_take_one_per_source(rows, chosen_ids=chosen_ids, limit=limit))
    if len(selected) < limit:
        selected.extend(
            _round_robin_fill(rows, chosen_ids=chosen_ids, remaining=limit - len(selected))
        )
    return selected


def _search_quota(limit: int) -> int:
    if limit <= 3:
        return 1
    return min(3, max(2, limit // 4))


def select_rows_for_notification(rows: list[dict], *, limit: int) -> list[dict]:
    now = datetime.now()
    valid_rows = [
        row
        for row in rows
        if row.get("published_at") is not None
        and (_days_since(row.get("published_at"), now=now) or 0) <= MAX_NOTIFY_AGE_DAYS
    ]
    fresh_rows = [
        row
        for row in valid_rows
        if (_days_since(row.get("published_at"), now=now) or 0) <= PREFERRED_NOTIFY_AGE_DAYS
    ]
    recent_rows = [
        row
        for row in valid_rows
        if PREFERRED_NOTIFY_AGE_DAYS < (_days_since(row.get("published_at"), now=now) or 0) <= MAX_NOTIFY_AGE_DAYS
    ]

    site_fresh = [row for row in fresh_rows if not _is_search_source(row)]
    site_recent = [row for row in recent_rows if not _is_search_source(row)]
    search_fresh = [row for row in fresh_rows if _is_search_source(row)]
    search_recent = [row for row in recent_rows if _is_search_source(row)]

    chosen_ids: set[int] = set()
    selected: list[dict] = []

    all_search_rows = search_fresh + search_recent
    search_cap = min(_search_quota(limit), len(all_search_rows))
    site_target = max(0, limit - search_cap)

    selected.extend(_select_from_bucket(site_fresh, limit=site_target, chosen_ids=chosen_ids))
    if len(selected) < site_target:
        selected.extend(
            _select_from_bucket(
                site_recent,
                limit=site_target - len(selected),
                chosen_ids=chosen_ids,
            )
        )

    search_selected = _select_from_bucket(search_fresh, limit=search_cap, chosen_ids=chosen_ids)
    if len(search_selected) < search_cap:
        search_selected.extend(
            _select_from_bucket(
                search_recent,
                limit=search_cap - len(search_selected),
                chosen_ids=chosen_ids,
            )
        )
    selected.extend(search_selected)

    if len(selected) < limit:
        selected.extend(
            _select_from_bucket(
                site_fresh + site_recent,
                limit=limit - len(selected),
                chosen_ids=chosen_ids,
            )
        )
    if len(selected) < limit:
        selected.extend(
            _select_from_bucket(
                all_search_rows,
                limit=limit - len(selected),
                chosen_ids=chosen_ids,
            )
        )
    return _sort_rows(selected)[:limit]


def build_weekly_summary_message(
    rows: list[dict],
    *,
    source_key: str | None = None,
) -> str:
    if not rows:
        scope = source_key or "全部来源"
        return f"本周成果线索汇总：{scope} 暂无符合条件的新线索。"

    source_counter = Counter(str(row["source_name"] or "未知来源") for row in rows)
    source_distribution = "、".join(
        f"{name}{count}条" for name, count in source_counter.most_common()
    )

    lines = [
        "本周科技成果线索汇总",
        f"范围：{source_key or '全部来源'}",
        f"条数：{len(rows)}",
        f"单位分布：{source_distribution}",
        "说明：优先推送近 7 天线索，最晚不超过 30 天，并混合官网来源与搜索补充来源。",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                f"{index}. 单位：{row['source_name']}",
                f"发布时间：{_format_published_at(row['published_at'])}",
                f"标题：{row['title']}",
                f"类型：{_type_label(row['clue_type'])}",
                f"核心技术：{_truncate(row['core_technology'], 80)}",
                f"简介：{_truncate(row['summary'], 120)}",
                "链接：查看原文",
                "",
            ]
        )
    return "\n".join(lines).strip()


def build_weekly_summary_post(
    rows: list[dict],
    *,
    source_key: str | None = None,
) -> tuple[str, list[list[dict]]]:
    source_counter = Counter(str(row["source_name"] or "未知来源") for row in rows)
    source_distribution = "、".join(
        f"{name}{count}条" for name, count in source_counter.most_common()
    )
    title = "本周科技成果线索汇总"
    subtitle_scope = source_key or "全部来源"
    lines: list[list[dict]] = [
        [{"tag": "text", "text": f"范围：{subtitle_scope}"}],
        [{"tag": "text", "text": f"条数：{len(rows)}"}],
        [{"tag": "text", "text": f"单位分布：{source_distribution}"}],
        [{"tag": "text", "text": "说明：优先推送近 7 天线索，最晚不超过 30 天，并混合官网来源与搜索补充来源。"}],
    ]
    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                [{"tag": "text", "text": f"{index}. 单位：{row['source_name']}"}],
                [{"tag": "text", "text": f"发布时间：{_format_published_at(row['published_at'])}"}],
                [{"tag": "text", "text": f"标题：{row['title']}"}],
                [{"tag": "text", "text": f"类型：{_type_label(row['clue_type'])}"}],
                [{"tag": "text", "text": f"核心技术：{_truncate(row['core_technology'], 80)}"}],
                [{"tag": "text", "text": f"简介：{_truncate(row['summary'], 120)}"}],
                [{"tag": "a", "text": "查看原文", "href": row["page_url"]}],
            ]
        )
    return title, lines


def send_feishu_test(settings: Settings, text: str) -> dict:
    notifier = _build_feishu_notifier(settings)
    return notifier.send_text(text)


def notify_clue_pool(
    engine: Engine,
    settings: Settings,
    *,
    source_key: str | None,
    limit: int,
    resend: bool = False,
) -> dict[str, int]:
    notifier = _build_feishu_notifier(settings)
    candidate_rows = fetch_clue_pool(
        engine,
        source_key=source_key,
        limit=max(limit * 8, 80),
        only_unpushed=not resend,
        max_age_days=MAX_NOTIFY_AGE_DAYS,
        require_published_at=True,
    )
    selected_rows = select_rows_for_notification(candidate_rows, limit=limit)
    stats = NotifyStats(scanned_docs=len(candidate_rows))
    if not selected_rows:
        return stats.to_dict()

    title, lines = build_weekly_summary_post(selected_rows, source_key=source_key)
    notifier.send_post(title=title, lines=lines)
    stats.sent_docs = len(selected_rows)
    stats.sent_messages = 1
    if not resend:
        mark_clue_pool_pushed(engine, [int(row["id"]) for row in selected_rows])
    return stats.to_dict()
