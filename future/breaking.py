import re
from collections import defaultdict, deque
from datetime import datetime, timedelta

from src import config
from src.collectors.naver_news import dedupe

# 키워드별 최근 구간 기사 수 이력 (급증 기준선용)
_window_counts = defaultdict(lambda: deque(maxlen=config.SURGE_BASELINE_WINDOWS))
# 이미 발송한 이벤트 서명
_alerted = set()
# 구독자별 마지막 긴급 발송 시각 (쿨다운용)
_last_sent = {}


def reset_state():
    """테스트/재시작용 상태 초기화."""
    _window_counts.clear()
    _alerted.clear()
    _last_sent.clear()


def _has_breaking_keyword(item):
    """A: 제목/본문에 긴급 키워드가 포함되어 있는가."""
    text = f"{item.get('title', '')} {item.get('description', '')}"
    return any(kw in text for kw in config.BREAKING_KEYWORDS)


def _published_within(item, now, minutes):
    """급증 카운트 대상: 시각이 확실하고 최근 구간 내에 게시된 기사만."""
    iso = item.get("published_at")
    if not iso:
        return False
    try:
        published = datetime.fromisoformat(iso)
    except ValueError:
        return False
    return published >= now - timedelta(minutes=minutes)


def surge_factor(keyword, count):
    """이번 구간 기사 수를 기준선(과거 평균)과 비교한 배수를 반환하고 이력에 반영.

    기준선이 아직 없으면(초기 구간) 0.0 을 반환해 급증으로 오판하지 않는다.
    """
    history = _window_counts[keyword]
    baseline = (sum(history) / len(history)) if history else 0
    history.append(count)  # 이번 카운트를 다음 기준선에 반영
    if baseline <= 0:
        return 0.0
    return count / baseline


def _signature(keyword, lead):
    """이벤트 중복 판정용 서명 (키워드 + 대표기사 링크/정규화 제목)."""
    title_key = re.sub(r"\s+", " ", lead.get("title", "")).strip().lower()
    return f"{keyword}|{lead.get('link', '')}|{title_key}"


def detect(collected, now):
    """{keyword: [item]} 에서 발송할 속보 이벤트 목록을 반환.

    각 키워드마다 최근 구간(MONITOR_INTERVAL) 기사 수로 급증(B)을,
    긴급 키워드 포함으로 A 를 판정해 (A∧B) or (강한 B) 이면 이벤트화한다.
    """
    events = []
    for keyword, items in collected.items():
        recent = [it for it in items if _published_within(it, now, config.MONITOR_INTERVAL_MINUTES)]
        count = len(recent)
        factor = surge_factor(keyword, count)

        b = factor >= config.SURGE_FACTOR and count >= config.SURGE_MIN_COUNT
        b_strong = factor >= config.STRONG_SURGE_FACTOR and count >= config.SURGE_MIN_COUNT
        hits = [it for it in recent if _has_breaking_keyword(it)]
        a = bool(hits)

        if (a and b) or b_strong:
            lead = hits[0] if hits else recent[0]
            events.append({
                "keyword": keyword,
                "headline": lead.get("title", ""),
                "link": lead.get("link", ""),
                "items": dedupe(recent),   # 발송 본문은 근접 중복 제거
                "signature": _signature(keyword, lead),
                "factor": round(factor, 2),
            })
    return events


def is_new_event(event):
    """이미 발송한 이벤트면 False(중복). 처음이면 기록하고 True."""
    sig = event["signature"]
    if sig in _alerted:
        return False
    _alerted.add(sig)
    return True


def in_cooldown(email, now):
    """구독자가 쿨다운(최근 EMERGENCY_COOLDOWN_MINUTES 내 발송) 중인가."""
    last = _last_sent.get(email)
    return last is not None and (now - last) < timedelta(minutes=config.EMERGENCY_COOLDOWN_MINUTES)


def mark_sent(email, now):
    """구독자에게 긴급 발송했음을 기록(쿨다운 시작)."""
    _last_sent[email] = now
