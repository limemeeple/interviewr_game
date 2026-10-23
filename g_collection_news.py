import html
import json
import re
import urllib.parse
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import feedparser
from sqlalchemy import text

from db import get_conn

KST = ZoneInfo("Asia/Seoul")
MAX_ITEMS = 20   # 키워드당 가져올 기사 수
RSS_URL = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko"


# ---------- 2단계: 기사 하나 정리 ----------
def parse_entry(entry) -> dict:
    raw_title = html.unescape(entry.title).strip()
    if " - " in raw_title:
        title, press = raw_title.rsplit(" - ", 1)
    else:
        title, press = raw_title, ""

    press = re.sub(r"^(https?://)?(www\.)?", "", press.strip()).rstrip("/")

    pub = parsedate_to_datetime(entry.published)
    pub_kst = pub.astimezone(KST).replace(tzinfo=None)

    return {
        "title": title.strip(),
        "press": press,
        "pub_date": pub_kst,
        "link": entry.link,
    }


# ---------- 키워드 하나로 기사 목록 받기 ----------
def fetch_news(keyword: str) -> list[dict]:
    url = RSS_URL.format(q=urllib.parse.quote(keyword))
    feed = feedparser.parse(url)
    return [parse_entry(e) for e in feed.entries[:MAX_ITEMS]]


# ---------- 4단계(새로 추가): DB에 저장 ----------
def save_articles(conn, company_id: int, keyword: str, articles: list[dict]) -> int:
    saved = 0
    for a in articles:
        result = conn.execute(
            text("""
                INSERT IGNORE INTO news_articles
                    (company_id, keyword, title, press, description, link, pub_date)
                VALUES
                    (:company_id, :keyword, :title, :press, :description, :link, :pub_date)
            """),
            {
                "company_id": company_id,
                "keyword": keyword,
                "title": a["title"],
                "press": a["press"],
                "description": a["title"],   # 구글 RSS는 요약이 없어서 제목으로 채움
                "link": a["link"],
                "pub_date": a["pub_date"],
            },
        )
        saved += result.rowcount   # 새로 들어가면 1, 중복이라 건너뛰면 0
    return saved


# ---------- 실행 ----------
with get_conn() as conn:
    companies = conn.execute(
        text("SELECT id, name, keywords FROM companies WHERE use_yn = 'Y'")
    ).all()

    for company_id, name, keywords_json in companies:
        total = 0
        for kw in json.loads(keywords_json):
            articles = fetch_news(kw)
            total += save_articles(conn, company_id, kw, articles)
        conn.commit()   # 회사 하나 끝날 때마다 확정
        print(f"[{name}] 새 기사 {total}건 저장")