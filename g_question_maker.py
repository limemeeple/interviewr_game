import json
import random
import re

import time
from datetime import timedelta, datetime

from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import ChatOllama
from sqlalchemy import text

from db import get_conn
from g_load_doc import coll
from g_load_news import newscoll

llm = ChatOllama(
    model="gemma4:e4b",
    reasoning=False,
    num_ctx=8192,
    temperature=0.7,
    keep_alive="1h"
)

RECENT_DAYS     = 30    # 최근 몇일 기사 쓸지 기준.
SIMILAR_DIST    = 0.15  # 중복기사로 볼 거리 기준.

# JSON으로만 답하게 하는 모델을 추가.
# 시사의 경우 질문과 정답을 DB에서 전달해 주기 때문에.
json_llm = ChatOllama(
    model="gemma4:e4b",
    reasoning=False,
    num_ctx=8192,
    temperature=0.7,
    keep_alive="1h",
    format="json"
)

# 체크한 문서에서 청크 하나 고르기
# 이미 사용한 청크는 제외 한다.
def pick_chunk(document_ids: list[int], used_ids: set[str]) -> dict | None:
    res = coll.get(
        where={"document_id":{"$in": document_ids}},
        include=["documents", "metadatas"]
    )

    # id = i, text = d, page, document_id = m
    # res 안에는 ids, documents, metadatas 3개의 리스트가 들어가 있다.
    # metadatas 안에는 {document_id:값, page:값} 들어가 있음.
    # for 문이 돌면서 해당 값들을 넣어준다.
    # used_ids는 이미 사용된 청크 정보이다.
    candidates = [
        {"id": i, "text": d, "page": m["page"], "document_id": m["document_id"]}
        for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])
        if i not in used_ids
    ]
    return random.choice(candidates) if candidates else None

# 면접관 정보 가져오기
def get_interviewer(code: str) -> dict:

    sql = text("SELECT code, name, persona_prompt FROM interviewers WHERE code = :code")

    with get_conn() as conn:
        result = conn.execute(sql, {"code": code}).mappings().one()
    return dict(result)


# 2026-10-22
# 테스트 결과 질문은 제대로 나왔으나 질문 내용에 문제가 있었음.(ex> 개념 설명이 아닌 개발 환경 세팅 페이지에서 질문을 해버림, 처음 질문인데 대답한 질문에 꼬리 질문 처럼 물어봄)
# 이 부분 수정에 대한 도움을 받았는데 내용은 아래와 같다.
# 1. 프롬프터를 구체화 한다, 2. DB에 면접자 성격을 더 상세히 서술한다. 3. 제대로 된 질문이 아닐경우 청크를 스킵하게 해준다.
answer_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "너는 면접관 '{name}'이다.\n"
     "성격과 말투: {persona}\n\n"
     "[자료]는 지원자가 미리 공부한 학습 자료다.\n"
     "규칙:\n"
     "- 자료 속 기술 개념, 함수의 역할, 원리, 차이점, 사용 이유 중 하나를 골라 질문한다.\n"
     "- 파일 업로드, 설치, 폴더 경로, 실습 환경 설정 같은 절차는 묻지 않는다.\n"
     "- '자료에 따르면', '~라고 했는데'처럼 자료를 언급하지 말고 지원자에게 직접 묻는다.\n"
     "- question: 한두 문장의 질문. 힌트는 넣지 않는다.\n"
     "- keywords: 좋은 답변에 꼭 들어가야 하는 핵심 단어 2~4개. 질문에 이미 나온 단어는 뺀다.\n"
     "- hint: {hint_rule}\n"
     "- 자료에 질문할 만한 개념이 없으면 {{\"skip\": true}} 만 출력한다.\n"
     "출력은 JSON 하나: {{\"question\": \"...\", \"keywords\": [\"...\"], \"hint\": \"...\"}}"),
    ("human", "[자료]\n{context}"),
])

edu_chain = answer_prompt | json_llm

HINT_RULE = {
    "GOOD": "생각의 방향만 알려주는 한 문장. keywords의 단어나 정답 내용은 절대 쓰지 않는다.",
    "NORMAL": "빈 문자열 \"\"",
    "BAD": "빈 문자열 \"\"",
}

# 시사(기사를 이용한 질문)을 만드는 프롬프터이다.
current_prompt = ChatPromptTemplate.from_messages([
    ("system",
     "너는 면접관 '{name}'이다.\n"
     "성격과 말투: {persona}\n"
     "지원 회사: {company} ({industry} 업종)\n\n"
     "[기사 제목]을 보고, 지원자가 최근 업계 소식을 아는지 확인하는 퀴즈를 만든다.\n"
     "규칙:\n"
     "- 정답 종류(answer_type)는 지역, 기관, 기업, 품목, 숫자, 인물 중 하나다.\n"
     "- 정답(answer)은 제목에 있는 한 단어를 그대로 쓴다. 띄어쓰기가 있는 구절은 안 된다.\n"
     "- 질문은 반드시 그 정답 종류를 묻는다. 예: 지역이면 '어느 지역', 숫자면 '몇', 기관이면 '어떤 기관'.\n"
     "- 질문에 정답이나 정답과 같은 뜻의 말을 넣지 않는다.\n"
     "- 다음 단어는 정답으로 쓰지 않는다: {keywords}\n"
     "- 제목이 의견, 다짐, 구호, 연재 제목이라 구체적 사실이 없으면 skip 한다.\n"
     "- 질문은 한두 문장, 면접관의 말투로 한다.\n\n"
     "예시:\n"
     "제목: 제천시, 스마트팜으로 샤인머스캣 수확 성공\n"
     "{{\"answer_type\": \"품목\", \"answer\": \"샤인머스캣\", "
     "\"question\": \"제천시가 스마트팜으로 수확에 성공한 과일은 무엇일까요?\"}}\n"
     "제목: 경북도, 청년농 300명에 정착지원금 지급\n"
     "{{\"answer_type\": \"숫자\", \"answer\": \"300명\", "
     "\"question\": \"경북도가 정착지원금을 주기로 한 청년농은 몇 명일까요?\"}}\n"
     "제목: 농식품부 \"가격 안정 위해 최선 다하겠다\"\n"
     "{{\"skip\": true}}"),
    ("human", "제목: {title}"),
])

current_chain = current_prompt | json_llm

# 질문 만들기
def hint_leaks(hint: str, keywords: list[str]) -> bool:
    """힌트에 정답 핵심어가 들어 있으면 True"""
    h = norm(hint)
    return any(len(norm(k)) >= 2 and norm(k) in h for k in keywords)


def make_question(interviewer_code: str, document_ids: list[int], used_ids: set[str]) -> dict | None:
    iv = get_interviewer(interviewer_code)

    for _ in range(3):
        chunk = pick_chunk(document_ids, used_ids)
        if chunk is None:
            return None

        res = edu_chain.invoke({
            "name": iv["name"],
            "persona": iv["persona_prompt"],
            "hint_rule": HINT_RULE[iv["code"]],
            "context": chunk["text"],
        })

        try:
            data = json.loads(res.content)
        except json.JSONDecodeError:
            used_ids.add(chunk["id"])
            continue

        if data.get("skip"):
            used_ids.add(chunk["id"])
            print(f"  (SKIP: {chunk['id']} {chunk['page']}페이지)")
            continue

        question = str(data.get("question", "")).strip().strip('"“”')
        if not question:
            used_ids.add(chunk["id"])
            continue

        keywords = [str(k) for k in data.get("keywords", []) if str(k).strip()]
        hint = str(data.get("hint", "")).strip().strip('"“”')

        # 힌트에 정답이 새면 힌트를 버림
        if hint and hint_leaks(hint, keywords):
            print(f"  (힌트 버림: {hint} / 핵심어 {keywords})")
            hint = ""

        full = f"{question} {hint}".strip() if iv["code"] == "GOOD" else question
        return {
            "interviewer": iv["code"],
            "interviewer_name": iv["name"],
            "question": full,
            "hint": hint,
            "keywords": keywords,
            "source": chunk,
        }

    return None

###### 시사 질문 만들기(회사정보에 따른 기사 선택 후 질문 생성)
def get_company(company_id: int) -> dict:

    sql = text("SELECT id, name, industry, keywords FROM companies WHERE id = :id")

    with get_conn() as conn:
        result = conn.execute(sql, {"id": company_id}).mappings().one()

    company = dict(result)
    company["keywords"] = json.loads(company["keywords"])
    return company

# 신문기사 고르기
def pick_article(company_id: int, used_ids: set[str]) -> dict | None:
    since = int((datetime.now() - timedelta(days=RECENT_DAYS)).timestamp()) # 위에 설정한 기간이전 날짜를 구한다.

    res = newscoll.get(
        where={"$and": [
            {"company_id": company_id},
            {"pub_ts": {"$gte": since}},  # $gte는 크거나 같은걸 뜻한다. 반대는 $lte
        ]},
        include=["documents", "metadatas"],
    )
    candidates = [
        {"id": i, "title": d, "press": m["press"], "pub_ts": m["pub_ts"], "company_id": m["company_id"]}
        for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])
        if i not in used_ids
    ]
    return random.choice(candidates) if candidates else None

# 비슷한 기사 제외
def mark_similar_used(article: dict, used_ids: set[str]) -> None:
    res = newscoll.query(
        query_texts=[article["title"]],
        n_results=10,
        where={"company_id": article["company_id"]},
    )
    for i, dist, d in zip(res["ids"][0], res["distances"][0], res["documents"][0]):
        if dist <= SIMILAR_DIST: # 두 기사의 거리를 계산해서 위에 기준보다 가까울 경우 제외시킨다.
            used_ids.add(i)
            if i != article["id"]:
                print(f"  (비슷한 기사 제외: {dist:.3f} | {d})")

# 정답 비교시 필요한 공백 제거 및 소문자 처리.
def norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()

ANSWER_TYPES = {"지역", "기관", "기업", "품목", "숫자", "인물"}

# 시사 문제에 어떤 문제가 있는지 확인한다.
def check_quiz(question: str, answer: str, answer_type: str, title: str, keywords: list[str]) -> str | None:
    if not question or not answer:
        return "질문이나 정답이 비어 있음"
    if answer_type not in ANSWER_TYPES:
        return f"정답 종류가 이상함: {answer_type}"
    if " " in answer.strip() or len(answer) > 12:
        return f"정답이 한 단어가 아님: {answer}"
    if norm(answer) not in norm(title):
        return f"정답 '{answer}'이(가) 제목에 없음"
    if norm(answer) in norm(question):
        return "질문에 정답이 들어 있음"
    if any(norm(answer) == norm(k) for k in keywords):
        return f"정답이 검색 키워드 '{answer}'"
    return None

# 문제중에 답이 다수일 경우 그것도 정답 처리할 수 있게 배열화
def expand_answers(answer: str, title: str) -> list[str]:
    for group in re.findall(r"[^\s,…]+(?:[·、][^\s,…]+)+", title):
        items = [x.strip("'\"‘’“”") for x in re.split(r"[·、]", group)]
        if answer in items:
            return items
    return [answer]

# 시사 문제 만들기
def make_current_question(interviewer_code: str, company_id: int, used_ids: set[str]) -> dict | None:
    interviewers = get_interviewer(interviewer_code)
    company = get_company(company_id)

    for _ in range(3):
        article = pick_article(company_id, used_ids)
        if article is None:
            return None

        res = current_chain.invoke({
            "name": interviewers["name"],
            "persona": interviewers["persona_prompt"],
            "company": company["name"],
            "industry": company["industry"],
            "keywords": ", ".join(company["keywords"]),
            "title": article["title"],
        })

        try:
            data = json.loads(res.content)
        except json.JSONDecodeError:
            print(f"  (JSON 아님: {res.content[:200]!r})")
            used_ids.add(article["id"])
            continue

        if data.get("skip"):
            used_ids.add(article["id"])
            print(f"  (SKIP: {article['title']})")
            continue

        question = data.get("question", "").strip().strip('"“”')
        answer = data.get("answer", "").strip()
        answer_type = data.get("answer_type", "").strip()
        problem = check_quiz(question, answer, answer_type, article["title"], company["keywords"])
        if problem:
            used_ids.add(article["id"])
            print(f"  (버림: {problem} | {article['title']})")
            continue

        mark_similar_used(article, used_ids)
        answers = expand_answers(answer, article["title"])
        return {
            "interviewer": interviewers["code"],
            "interviewer_name": interviewers["name"],
            "question": question,
            "answer_type": answer_type,
            "answer_key": "|".join(answers),
            "source": article,
        }

    return None

# 시사 문제 채점하기
# 시사 문제는 답이 있기 때문에 LLM 채점이 필요 없음.
def grade_one(u: str, a: str) -> int:
    if u == a:
        return 100
    if a in u and len(u) - len(a) <= 2:    # "토마토요"
        return 100
    if u in a and len(a) - len(u) <= 1:    # "300" ↔ "300명", "파프리카" ↔ "파프리카값"
        return 100
    return 0

# 정답이 2개일 경우 (A|B) 정답 처리.
def grade_current(user_answer: str, answer_key: str) -> int:
    u = norm(user_answer)
    if not u:
        return 0
    return max(grade_one(u, norm(a)) for a in answer_key.split("|"))

if __name__ == "__main__":
    import os

    print("store 위치:", os.path.abspath("./store"))
    print("news 컬렉션 전체 개수:", newscoll.count())
    sample = newscoll.get(limit=2, include=["documents", "metadatas"])
    print("샘플:", sample["ids"], sample["metadatas"])
    print(expand_answers("애호박", "애호박·파프리카값 반토막…정부, 할인 카드로 '농가 구하기'"))
    print(grade_current("파프리카", "애호박|파프리카값"))
    used = set()
    for code in ["GOOD", "NORMAL", "BAD"]:
        start = time.time()
        q = make_current_question(code, 1, used)   # 1 = 푸른들팜
        if q is None:
            print("낼 수 있는 기사가 없음")
            break

        print("-" * 50)
        print(f"[{q['interviewer_name']}] ({time.time() - start:.1f}초)")
        print(f"기사: {q['source']['press']} | {q['source']['title']}")
        print(f"질문: {q['question']}")
        print(f"정답: {q['answer_key']}")
        print(f"채점 테스트: 정답 입력 → {grade_current(q['answer_key'] + '요', q['answer_key'])}점, "
              f"오답 입력 → {grade_current('모르겠어요', q['answer_key'])}점")
        for alt in q["answer_key"].split("|")[1:]:
            print(f"  다른 정답 '{alt}' 입력 → {grade_current(alt, q['answer_key'])}점")