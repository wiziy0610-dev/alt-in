"""
Alt-In 대체투자 뉴스 스크래퍼 (무료 버전)
------------------------------------------
의존 패키지 설치:
    pip install requests beautifulsoup4

API 키 불필요 — 완전 무료로 작동합니다.
"""

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
# 설정
# ──────────────────────────────────────────────

KEYWORDS = [
    "대체투자", "사모펀드", "인프라 투자", "부동산 펀드",
    "벤처캐피탈", "PEF", "블라인드펀드", "리츠 투자",
    "세컨더리 펀드", "해외 인프라",
]

# 키워드 → 카테고리 매핑
CATEGORY_MAP = {
    "부동산 펀드": "부동산",
    "리츠 투자":   "부동산",
    "인프라 투자": "인프라",
    "해외 인프라": "인프라",
    "사모펀드":    "PE",
    "PEF":        "PE",
    "블라인드펀드":"PE",
    "세컨더리 펀드":"PE",
    "벤처캐피탈":  "VC",
    "대체투자":    "부동산",  # 광범위 키워드는 부동산으로 분류
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

REQUEST_DELAY = 1.5   # 요청 간 대기(초) — 서버 부하 방지
MAX_PER_KEYWORD = 5   # 키워드당 최대 수집 기사 수
MIN_BODY_LEN = 100    # 본문 최소 길이(자)
SUMMARY_SENTENCES = 3 # 자동 요약 문장 수


# ──────────────────────────────────────────────
# 데이터 모델
# ──────────────────────────────────────────────

@dataclass
class Article:
    id:         int
    title:      str
    url:        str
    source:     str
    keyword:    str
    category:   str
    tag:        str
    summary:    list[str] = field(default_factory=list)
    time:       str = ""
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ──────────────────────────────────────────────
# 태그 자동 분류 (키워드 기반)
# ──────────────────────────────────────────────

TAG_RULES = [
    (["리츠", "REITs", "공모"], "리츠"),
    (["오피스", "사무실", "빌딩", "CBD"], "오피스"),
    (["물류", "창고", "센터"], "물류"),
    (["풍력", "태양광", "에너지", "수소"], "에너지"),
    (["GTX", "도로", "철도", "공항", "교통"], "교통"),
    (["데이터센터", "AI", "인공지능"], "AI"),
    (["바이아웃", "인수", "M&A"], "바이아웃"),
    (["IPO", "상장", "엑시트"], "엑시트"),
    (["세컨더리"], "세컨더리"),
    (["펀드결성", "펀드 결성", "출자"], "펀드결성"),
    (["바이오", "제약", "헬스케어"], "바이오"),
    (["디지털", "클라우드", "IT"], "디지털"),
]

def assign_tag(title: str) -> str:
    for keywords, tag in TAG_RULES:
        if any(kw in title for kw in keywords):
            return tag
    return "대체투자"


# ──────────────────────────────────────────────
# 네이버 뉴스 검색
# ──────────────────────────────────────────────

def search_naver_news(keyword: str, max_results: int = MAX_PER_KEYWORD) -> list[dict]:
    """네이버 뉴스 검색 결과에서 제목·링크·출처·시간을 가져옵니다."""
    url = "https://search.naver.com/search.naver"
    params = {"where": "news", "query": keyword, "sm": "tab_jum"}

    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("네이버 검색 실패 [%s]: %s", keyword, e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for item in soup.select("div.news_wrap")[:max_results]:
        a_tag  = item.select_one("a.news_tit")
        press  = item.select_one("a.info.press")
        time_  = item.select_one("span.info")  # 시간 정보

        if not a_tag:
            continue

        results.append({
            "title":  a_tag.get_text(strip=True),
            "url":    a_tag["href"],
            "source": press.get_text(strip=True) if press else "출처 불명",
            "time":   time_.get_text(strip=True) if time_ else "방금 전",
        })

    log.info("  [%s] → %d건", keyword, len(results))
    return results


# ──────────────────────────────────────────────
# 본문 추출
# ──────────────────────────────────────────────

# 언론사별 본문 CSS 셀렉터
SOURCE_SELECTORS = {
    "한국경제":   ["div#articlebody", "div.article-body"],
    "매일경제":   ["div#article_body", "div.news_cnt_detail_wrap"],
    "조선비즈":   ["div#news_body_id"],
    "더벨":      ["div.article_view"],
    "딜사이트":   ["div.view_con"],
}
FALLBACK_SELECTORS = [
    "div#articleBodyContents", "div#articeBody", "div#newsct_article",
    "article", "div.article_body", "div.news-content", "div#content",
]

def extract_body(url: str, source: str) -> str:
    """기사 URL에서 본문 텍스트를 추출합니다."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("    본문 요청 실패: %s", e)
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # 광고·스크립트 제거
    for tag in soup(["script", "style", "figure", "iframe", "aside"]):
        tag.decompose()

    selectors = SOURCE_SELECTORS.get(source, []) + FALLBACK_SELECTORS
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator=" ", strip=True)
            if len(text) >= MIN_BODY_LEN:
                return text

    # 최후 수단: <p> 태그 수집
    paras = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 30]
    return " ".join(paras)


# ──────────────────────────────────────────────
# 본문 → 자동 요약 (AI 없이 문장 추출)
# ──────────────────────────────────────────────

def auto_summarize(title: str, body: str, n: int = SUMMARY_SENTENCES) -> list[str]:
    """
    본문을 마침표 기준으로 분리해 앞 n문장을 요약으로 사용합니다.
    본문이 없으면 제목을 활용한 기본 문장을 반환합니다.
    """
    if not body or len(body) < MIN_BODY_LEN:
        return [
            f"{title}에 관한 기사입니다.",
            "본문을 불러오지 못했습니다.",
            "링크를 클릭해 원문을 확인하세요.",
        ]

    # 문장 분리 (마침표·느낌표·물음표 기준)
    import re
    raw_sentences = re.split(r"(?<=[.!?])\s+", body)

    # 너무 짧거나 의미 없는 문장 제거
    sentences = [
        s.strip() for s in raw_sentences
        if len(s.strip()) > 20 and not s.strip().startswith("©")
    ]

    # 앞에서 n개 선택, 부족하면 있는 만큼
    selected = sentences[:n]

    # n개 미만이면 빈 슬롯 채우기
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

    for keyword in KEYWORDS:
        log.info("키워드 처리 중: [%s]", keyword)
        items = search_naver_news(keyword)

        for item in items:
            url = item["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # 1) 본문 추출
            log.info("  본문 추출: %s", item["title"][:40])
            body = extract_body(url, item["source"])
            time.sleep(REQUEST_DELAY)

            # 2) 자동 요약 (AI 없이)
            summary = auto_summarize(item["title"], body)

            art = Article(
                id       = article_id,
                title    = item["title"],
                url      = url,
                source   = item["source"],
                keyword  = keyword,
                category = CATEGORY_MAP.get(keyword, "부동산"),
                tag      = assign_tag(item["title"]),
                summary  = summary,
                time     = item["time"],
            )
            articles.append(art)
            article_id += 1
            log.info("  완료: %s", art.title[:50])

    return articles


def save_results(articles: list[Article], path: str = "alt_in_news.json") -> None:
    """결과를 JSON 파일로 저장합니다 (body 제외)."""
    data = [asdict(a) for a in articles]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("저장 완료: %s (%d건)", path, len(data))


# ──────────────────────────────────────────────
# 실행
# ──────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=== Alt-In 스크래퍼 시작 (무료 버전) ===")
    results = run_pipeline()
    save_results(results)

    print(f"\n총 {len(results)}건 수집 완료\n")
    for i, art in enumerate(results[:3], 1):
        print(f"[{i}] {art.title}")
        print(f"    출처: {art.source} | 카테고리: {art.category} | 태그: {art.tag}")
        for s in art.summary:
            print(f"    • {s}")
        print()
