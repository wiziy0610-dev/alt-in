"""
Alt-In 대체투자 뉴스 스크래퍼 (RSS 버전)
-----------------------------------------
의존 패키지 설치:
    pip install requests beautifulsoup4

API 키 불필요, 차단 없음, 완전 무료.
"""

import re
import time
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 대체투자 관련 키워드
# ──────────────────────────────────────────────

KEYWORDS = [
    "대체투자", "사모펀드", "PEF", "인프라 투자", "부동산 펀드",
    "벤처캐피탈", "블라인드펀드", "리츠", "세컨더리",
    "해외 인프라", "바이아웃", "모태펀드",
]

# ──────────────────────────────────────────────
# RSS 피드 목록 (공식 제공, 차단 없음)
# ──────────────────────────────────────────────

RSS_FEEDS = [
    # 네이버 뉴스 RSS (키워드 검색)
    {"url": "https://news.naver.com/main/rss/rss.nhn?oid=009", "source": "매일경제"},
    {"url": "https://news.naver.com/main/rss/rss.nhn?oid=015", "source": "한국경제"},
    {"url": "https://news.naver.com/main/rss/rss.nhn?oid=014", "source": "파이낸셜뉴스"},
    {"url": "https://news.naver.com/main/rss/rss.nhn?oid=469", "source": "한국경제TV"},
    # 구글 뉴스 RSS (키워드별)
    {"url": "https://news.google.com/rss/search?q=대체투자&hl=ko&gl=KR&ceid=KR:ko",      "source": "구글뉴스"},
    {"url": "https://news.google.com/rss/search?q=사모펀드+PEF&hl=ko&gl=KR&ceid=KR:ko", "source": "구글뉴스"},
    {"url": "https://news.google.com/rss/search?q=인프라투자+펀드&hl=ko&gl=KR&ceid=KR:ko","source": "구글뉴스"},
    {"url": "https://news.google.com/rss/search?q=벤처캐피탈+VC+투자&hl=ko&gl=KR&ceid=KR:ko","source": "구글뉴스"},
    {"url": "https://news.google.com/rss/search?q=리츠+부동산펀드&hl=ko&gl=KR&ceid=KR:ko","source": "구글뉴스"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

REQUEST_DELAY = 1.0
MAX_PER_FEED  = 10   # 피드당 최대 기사 수
MIN_BODY_LEN  = 80


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class Article:
    id:         int
    title:      str
    url:        str
    source:     str
    category:   str
    tag:        str
    summary:    list[str] = field(default_factory=list)
    time:       str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ──────────────────────────────────────────────
# 카테고리 & 태그 자동 분류
# ──────────────────────────────────────────────

CATEGORY_RULES = [
    (["리츠", "부동산", "오피스", "물류", "빌딩", "임대"], "부동산"),
    (["인프라", "풍력", "태양광", "에너지", "GTX", "도로", "철도", "공항", "수소", "데이터센터"], "인프라"),
    (["사모펀드", "PEF", "바이아웃", "세컨더리", "블라인드", "엑시트", "IPO", "M&A"], "PE"),
    (["벤처", "VC", "스타트업", "모태펀드", "시리즈", "투자 유치"], "VC"),
]

TAG_RULES = [
    (["리츠", "REITs"],               "리츠"),
    (["오피스", "빌딩", "CBD"],        "오피스"),
    (["물류", "창고"],                 "물류"),
    (["풍력", "태양광", "에너지", "수소"], "에너지"),
    (["GTX", "도로", "철도", "공항"],  "교통"),
    (["데이터센터", "클라우드"],        "디지털"),
    (["바이아웃", "인수"],             "바이아웃"),
    (["IPO", "상장", "엑시트"],        "엑시트"),
    (["세컨더리"],                     "세컨더리"),
    (["모태펀드", "펀드 결성", "펀드결성", "출자"], "펀드결성"),
    (["바이오", "제약", "헬스케어"],    "바이오"),
    (["AI", "인공지능"],               "AI"),
]

def assign_category(title: str) -> str:
    for keywords, cat in CATEGORY_RULES:
        if any(kw in title for kw in keywords):
            return cat
    return "부동산"  # 기본값

def assign_tag(title: str) -> str:
    for keywords, tag in TAG_RULES:
        if any(kw in title for kw in keywords):
            return tag
    return "대체투자"


# ──────────────────────────────────────────────
# 대체투자 관련 기사인지 필터링
# ──────────────────────────────────────────────

def is_relevant(title: str) -> bool:
    return any(kw in title for kw in KEYWORDS)


# ──────────────────────────────────────────────
# RSS 피드 파싱
# ──────────────────────────────────────────────

def parse_rss(feed: dict) -> list[dict]:
    try:
        resp = requests.get(feed["url"], headers=HEADERS, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        log.warning("RSS 요청 실패 [%s]: %s", feed["url"][:50], e)
        return []

    soup = BeautifulSoup(resp.text, "xml")
    items = soup.find_all("item")[:MAX_PER_FEED]
    results = []

    for item in items:
        title = item.find("title")
        link  = item.find("link")
        pub   = item.find("pubDate")
        src   = item.find("source")

        if not title or not link:
            continue

        title_text = title.get_text(strip=True)
        if not is_relevant(title_text):
            continue

        # 발행 시간 → 한국어 표현으로 변환
        time_str = "방금 전"
        if pub:
            try:
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub.get_text(strip=True))
                diff = datetime.now(pub_dt.tzinfo) - pub_dt
                h = int(diff.total_seconds() // 3600)
                if h < 1:
                    time_str = "방금 전"
                elif h < 24:
                    time_str = f"{h}시간 전"
                else:
                    time_str = f"{h // 24}일 전"
            except Exception:
                pass

        source = feed["source"]
        if src and src.get_text(strip=True):
            source = src.get_text(strip=True)

        results.append({
            "title":  title_text,
            "url":    link.get_text(strip=True),
            "source": source,
            "time":   time_str,
        })

    log.info("  [%s] → %d건", feed["source"], len(results))
    return results


# ──────────────────────────────────────────────
# 본문 추출 & 자동 요약
# ──────────────────────────────────────────────

BODY_SELECTORS = [
    "div#articleBodyContents", "div#articeBody", "div#newsct_article",
    "div#article_body", "div.article_body", "article", "div#content",
]

def extract_body(url: str) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
    except Exception:
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "figure", "iframe"]):
        tag.decompose()

    for sel in BODY_SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator=" ", strip=True)
            if len(text) >= MIN_BODY_LEN:
                return text

    paras = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 30]
    return " ".join(paras)

def auto_summarize(title: str, body: str, n: int = 3) -> list[str]:
    if not body or len(body) < MIN_BODY_LEN:
        return [f"{title}에 관한 기사입니다.", "본문을 불러오지 못했습니다.", "링크를 클릭해 원문을 확인하세요."]

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if len(s.strip()) > 20 and "©" not in s]
    selected = sentences[:n]
    while len(selected) < n:
        selected.append("관련 내용은 원문 기사를 확인하세요.")
    return selected


# ──────────────────────────────────────────────
# 메인 파이프라인
# ──────────────────────────────────────────────

def run_pipeline() -> list[Article]:
    seen_urls: set[str] = set()
    articles:  list[Article] = []
    article_id = 1

    for feed in RSS_FEEDS:
        log.info("RSS 수집 중: %s", feed["source"])
        items = parse_rss(feed)
        time.sleep(REQUEST_DELAY)

        for item in items:
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            log.info("  본문 추출: %s", item["title"][:40])
            body    = extract_body(url)
            summary = auto_summarize(item["title"], body)
            time.sleep(REQUEST_DELAY)

            articles.append(Article(
                id       = article_id,
                title    = item["title"],
                url      = url,
                source   = item["source"],
                category = assign_category(item["title"]),
                tag      = assign_tag(item["title"]),
                summary  = summary,
                time     = item["time"],
            ))
            article_id += 1
            log.info("  완료: %s", item["title"][:50])

    return articles


def save_results(articles: list[Article], path: str = "alt_in_news.json") -> None:
    data = [asdict(a) for a in articles]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("저장 완료: %s (%d건)", path, len(data))


# ──────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=== Alt-In RSS 스크래퍼 시작 ===")
    results = run_pipeline()
    save_results(results)

    print(f"\n총 {len(results)}건 수집 완료\n")
    for i, art in enumerate(results[:3], 1):
        print(f"[{i}] {art.title}")
        print(f"    출처: {art.source} | 카테고리: {art.category} | 태그: {art.tag}")
        for s in art.summary:
            print(f"    • {s}")
        print()
