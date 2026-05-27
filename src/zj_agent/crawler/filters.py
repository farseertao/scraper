from __future__ import annotations

from ..ai.content_judge import ContentJudge, RetentionDecision
from .deduper import normalize_whitespace

BLOCKED_URL_HINTS = [
    "javascript:",
    "void(0)",
    "/manage/login",
    "login.html",
    "/admin",
]

LOGIN_TITLE_HINTS = [
    "用户登录",
    "统一身份认证",
    "后台管理",
    "管理员登录",
]

STRONG_LOGIN_BODY_HINTS = [
    "请输入用户名",
    "请输入密码",
    "请输入账号",
    "忘记密码",
]

TITLE_NOISE_HINTS = [
    "通知",
    "预告",
    "讲座",
    "讲坛",
    "论坛",
    "研讨会",
    "学术报告",
    "来访",
    "调研",
    "党建",
    "招聘",
    "招生",
    "工作会议",
    "管理办法",
    "案件处理结果通报",
    "保密",
    "安全教育",
    "概况",
    "学院动态",
    "简讯",
    "政策解读",
]

CONTENT_NOISE_HINTS = [
    "会议通知",
    "讲座预告",
    "安全教育日",
    "不端行为案件处理",
    "工作会议",
    "管理办法",
    "调研交流",
    "地方合作概况",
    "学院动态",
    "学术研讨会",
    "学术交流",
]

POOL_NOISE_HINTS = [
    "启动会",
    "开题",
    "开题会",
    "顺利召开",
    "举办",
    "研讨会",
    "论坛",
    "讲座",
    "讲坛",
    "学院动态",
    "工作会议",
    "来访",
    "调研",
    "认定",
    "获批",
    "喜讯",
    "荣获",
    "获奖",
    "表彰",
    "参展",
    "揭牌",
    "签约",
    "仪式",
]

POSITIVE_HINTS = [
    "科技成果",
    "成果转化",
    "技术转移",
    "技术转让",
    "技术许可",
    "专利转化",
    "专利",
    "发明专利",
    "专利奖",
    "科技奖",
    "获奖项目",
    "提名项目",
    "中试",
    "样机",
    "概念验证",
    "成果推介",
    "产业化",
    "应用场景",
    "关键技术",
    "核心技术",
    "技术突破",
    "研究进展",
    "科研成果",
    "重大突破",
    "新药",
    "机器人",
]

SEARCH_REQUIRED_HINTS = [
    "成果转化",
    "技术转让",
    "技术许可",
    "技术转移",
    "专利转化",
    "概念验证",
    "中试",
    "样机",
    "产业化",
    "落地转化",
    "科技计划项目",
    "重大科技计划项目",
    "重点研发计划项目",
    "项目公示",
]

SEARCH_NOISE_HINTS = [
    "强省建设",
    "十五五",
    "荣誉称号",
    "工人先锋号",
    "喜报",
    "专题培训会",
    "培训会",
    "招聘",
    "诚聘",
    "新闻动态",
    "工作部署",
    "政策解读",
    "实施意见",
]

DOC_TYPE_MAP = {
    "专利": "patent",
    "发明专利": "patent",
    "专利奖": "award",
    "科技奖": "award",
    "获奖项目": "award",
    "提名项目": "award",
    "概念验证": "poc",
    "中试": "pilot",
    "样机": "prototype",
    "成果转化": "transfer",
    "技术转移": "transfer",
    "技术转让": "transfer",
    "技术许可": "transfer",
    "研究进展": "achievement",
    "科技成果": "achievement",
    "科研成果": "achievement",
    "重大突破": "achievement",
}


def _contains_any(text: str, hints: list[str]) -> list[str]:
    return [hint for hint in hints if hint in text]


def is_blocked_url(url: str) -> bool:
    raw = (url or "").strip().lower()
    if not raw:
        return False
    if " " in raw or "|" in raw:
        return True
    return any(hint in raw for hint in BLOCKED_URL_HINTS)


def is_login_page(title: str, content: str) -> bool:
    title_text = normalize_whitespace(title)
    if title_text and any(hint in title_text for hint in LOGIN_TITLE_HINTS):
        return True
    body = normalize_whitespace(content[:1200])
    if not body:
        return False
    strong_hits = sum(1 for hint in STRONG_LOGIN_BODY_HINTS if hint in body)
    if strong_hits >= 2:
        return True
    if len(body) >= 800:
        return False
    weak_hits = sum(1 for hint in LOGIN_TITLE_HINTS if hint in body)
    return bool(weak_hits) and len(body) < 300


def is_noise_title(title: str) -> bool:
    raw = normalize_whitespace(title)
    if not raw:
        return False
    noise_hits = _contains_any(raw, TITLE_NOISE_HINTS)
    positive_hits = _contains_any(raw, POSITIVE_HINTS)
    return bool(noise_hits) and not bool(positive_hits)


def is_poolworthy_title(title: str) -> bool:
    raw = normalize_whitespace(title)
    if not raw:
        return False
    noise_hits = _contains_any(raw, POOL_NOISE_HINTS)
    positive_hits = _contains_any(raw, POSITIVE_HINTS)
    return not (bool(noise_hits) and not bool(positive_hits))


def _infer_doc_type(text: str) -> str:
    for hint, doc_type in DOC_TYPE_MAP.items():
        if hint in text:
            return doc_type
    return "achievement"


def evaluate_retention(
    title: str,
    content: str,
    attachment_names: list[str],
    content_judge: ContentJudge | None = None,
) -> RetentionDecision:
    combined = normalize_whitespace("\n".join([title, content[:2500], " ".join(attachment_names)]))
    if is_login_page(title, content):
        return RetentionDecision(
            keep=False,
            doc_type="noise",
            reason_tags=["login_page"],
            decision_reason="dropped because page looks like login or admin page",
        )

    title_noise_hits = _contains_any(normalize_whitespace(title), TITLE_NOISE_HINTS)
    content_noise_hits = _contains_any(combined, CONTENT_NOISE_HINTS)
    positive_hits = _contains_any(combined, POSITIVE_HINTS)

    if is_noise_title(title) and not positive_hits:
        return RetentionDecision(
            keep=False,
            doc_type="noise",
            reason_tags=title_noise_hits[:4] or ["title_noise"],
            decision_reason="dropped by title prefilter",
        )

    if content_noise_hits and not positive_hits:
        return RetentionDecision(
            keep=False,
            doc_type="noise",
            reason_tags=content_noise_hits[:4],
            decision_reason="dropped by noise content rules",
        )

    if positive_hits:
        return RetentionDecision(
            keep=True,
            doc_type=_infer_doc_type(" ".join(positive_hits)),
            reason_tags=positive_hits[:6],
            decision_reason="kept by rule hits",
        )

    if content_judge:
        ai_decision = content_judge.judge(title=title, content=content, attachment_names=attachment_names)
        if ai_decision is not None:
            return ai_decision

    return RetentionDecision(
        keep=False,
        doc_type="other",
        reason_tags=["uncertain"],
        decision_reason="dropped because no positive signals were found",
    )


def evaluate_search_source_retention(
    title: str,
    content: str,
    page_url: str,
) -> RetentionDecision | None:
    combined = normalize_whitespace("\n".join([title, content[:2000], page_url]))
    required_hits = _contains_any(combined, SEARCH_REQUIRED_HINTS)
    noise_hits = _contains_any(combined, SEARCH_NOISE_HINTS)

    if noise_hits and not required_hits:
        return RetentionDecision(
            keep=False,
            doc_type="noise",
            reason_tags=noise_hits[:4],
            decision_reason="dropped by search-source noise rules",
        )

    if not required_hits:
        return RetentionDecision(
            keep=False,
            doc_type="other",
            reason_tags=["search_weak_signal"],
            decision_reason="dropped because search-source result lacks conversion or project signals",
        )

    return None
