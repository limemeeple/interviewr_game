import json
import random

from g_question_maker import make_current_question, make_question

from datetime import datetime, timedelta

from sqlalchemy import text

from db import get_conn

from g_grade import grade_edu
from g_load_doc import coll
from g_load_news import newscoll
from g_question_maker import grade_current

MIN_MINUTES, MAX_MINUTES = 10, 60
CURRENT_RATE = 30 # 시사 문제가 나올 퍼센트

#아이템 종류
ITEMS = ["PASS", "TIME", "ENERGY"]
ITEM_COLUMNS = {"PASS": "item_pass", "TIME": "item_time", "ENERGY": "item_energy"}

#아이템 사용에 필요한 기준
TIME_BONUS_MIN = 3
ENERGY_BONUS = 20
MAX_ENERGY = 100

def start_game(user_id: str, document_ids: list[int], include_current: bool, minutes: int) -> int:
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValueError(f"제한 시간은 {MIN_MINUTES}~{MAX_MINUTES}분이어야 합니다")
    if not document_ids:
        raise ValueError("문서를 하나 이상 선택해야 합니다")

    with get_conn() as conn:

        my_docs = set(conn.execute(
            text("SELECT id FROM doc_upload WHERE user_id = :u AND status = 'READY'"),
            {"u": user_id},
        ).scalars().all())
        if not set(document_ids) <= my_docs:
            raise ValueError("사용할 수 없는 문서가 포함되어 있습니다")


        company = conn.execute(
            text("SELECT id, name, industry FROM companies WHERE use_yn = 'Y' ORDER BY RAND() LIMIT 1")
        ).mappings().one()


        now = datetime.now().replace(microsecond=0)
        result = conn.execute(
            text("""
                INSERT INTO game_history
                    (user_id, company_id, include_current, time_limit_sec, started_at, ends_at)
                VALUES
                    (:user_id, :company_id, :include_current, :sec, :started_at, :ends_at)
            """),
            {
                "user_id": user_id,
                "company_id": company["id"],
                "include_current": "Y" if include_current else "N",
                "sec": minutes * 60,
                "started_at": now,
                "ends_at": now + timedelta(minutes=minutes),
            },
        )
        session_id = result.lastrowid


        conn.execute(
            text("INSERT INTO session_documents (session_id, document_id) VALUES (:s, :d)"),
            [{"s": session_id, "d": d} for d in document_ids],
        )
        conn.commit()

    return session_id


def get_game_state(session_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            text("""
                SELECT g.*, c.name AS company_name, c.industry
                FROM game_history g
                JOIN companies c ON c.id = g.company_id
                WHERE g.id = :id
            """),
            {"id": session_id},
        ).mappings().one()

        doc_ids = conn.execute(
            text("SELECT document_id FROM session_documents WHERE session_id = :id"),
            {"id": session_id},
        ).scalars().all()

    remaining = int((row["ends_at"] - datetime.now()).total_seconds())
    return {
        "session_id": row["id"],
        "user_id": row["user_id"],
        "company_id": row["company_id"],
        "company_name": row["company_name"],
        "industry": row["industry"],
        "include_current": row["include_current"] == "Y",
        "document_ids": list(doc_ids),
        "energy": row["energy"],
        "items": {"PASS": row["item_pass"], "TIME": row["item_time"], "ENERGY": row["item_energy"]},
        "status": row["status"],
        "remaining_sec": max(0, remaining),
    }

def pick_interviewer(conn, company_id: int) -> str:
    row = conn.execute(
        text("SELECT good_percent, normal_percent, bad_percent FROM companies WHERE id = :id"),
        {"id": company_id},
    ).one()
    return random.choices(["GOOD", "NORMAL", "BAD"], weights=list(row))[0]


def load_used_ids(conn, session_id: int) -> set[str]:
    refs = conn.execute(
        text("SELECT source_refs FROM qa_turns WHERE session_id = :s AND source_refs IS NOT NULL"),
        {"s": session_id},
    ).scalars().all()
    used = set()
    for r in refs:
        used.update(json.loads(r).get("used", []))
    return used


def get_pending_turn(conn, session_id: int) -> dict | None:
    row = conn.execute(
        text("""
            SELECT t.id, t.turn_no, t.interviewer, i.name AS interviewer_name,
                   t.question_type, t.question
            FROM qa_turns t
            JOIN interviewers i ON i.code = t.interviewer
            WHERE t.session_id = :s AND t.answer IS NULL AND t.passed_by_item = 0
            ORDER BY t.id DESC
            LIMIT 1
        """),
        {"s": session_id},
    ).mappings().first()
    if row is None:
        return None
    return {
        "turn_id": row["id"], "turn_no": row["turn_no"],
        "interviewer": row["interviewer"], "interviewer_name": row["interviewer_name"],
        "question_type": row["question_type"], "question": row["question"],
    }

def check_owner(state: dict, user_id: str) -> None:
    if state["user_id"] != user_id:
        raise PermissionError("본인의 게임이 아닙니다")

def next_question(session_id: int, user_id: str, force_interviewer: str | None = None) -> dict | None:
    state = get_game_state(session_id)
    check_owner(state, user_id)
    if state["status"] != "PLAYING" or state["remaining_sec"] <= 0 or state["energy"] <= 0:
        return None

    with get_conn() as conn:
        pending = get_pending_turn(conn, session_id)
        if pending:
            return pending

        code = force_interviewer or pick_interviewer(conn, state["company_id"])
        used = load_used_ids(conn, session_id)
        turn_no = conn.execute(
            text("SELECT COUNT(*) FROM qa_turns WHERE session_id = :s"), {"s": session_id}
        ).scalar() + 1

    before = set(used)
    want_current = state["include_current"] and random.randint(1, 100) <= CURRENT_RATE

    q, qtype = None, None
    if want_current:
        q, qtype = make_current_question(code, state["company_id"], used), "CURRENT"
    if q is None:
        q, qtype = make_question(code, state["document_ids"], used), "EDU"
    if q is None and state["include_current"] and not want_current:
        q, qtype = make_current_question(code, state["company_id"], used), "CURRENT"
    if q is None:
        q, qtype = make_question(code, state["document_ids"], set()), "EDU"
    if q is None:
        return None

    refs = {
        "source_id": q["source"]["id"],
        "used": sorted((used - before) | {q["source"]["id"]}),
    }
    with get_conn() as conn:
        result = conn.execute(
            text("""
                INSERT INTO qa_turns
                    (session_id, turn_no, interviewer, question_type, question, answer_key, source_refs)
                VALUES
                    (:s, :turn_no, :iv, :qtype, :question, :answer_key, :refs)
            """),
            {
                "s": session_id, "turn_no": turn_no, "iv": code, "qtype": qtype,
                "question": q["question"],
                "answer_key": q.get("answer_key"),
                "refs": json.dumps(refs, ensure_ascii=False),
            },
        )
        conn.commit()

    return {
        "turn_id": result.lastrowid, "turn_no": turn_no,
        "interviewer": code, "interviewer_name": q["interviewer_name"],
        "question_type": qtype, "question": q["question"],
    }

def load_interviewers(conn) -> dict:
    rows = conn.execute(text("""
        SELECT code, name, pass_score, excellent_score, fail_minus_score, item_drop_rate
        FROM interviewers
    """)).mappings().all()
    return {r["code"]: dict(r) for r in rows}


def judge_result(score: int, iv: dict) -> str:
    if score >= iv["excellent_score"]:
        return "EXCELLENT"
    if score >= iv["pass_score"]:
        return "PASS"
    return "FAIL"


def pick_reactions(conn, interviewers: dict, score: int) -> dict:
    """세 면접관이 각자 기준으로 반응"""
    rows = conn.execute(text("SELECT interviewer, result, line FROM reaction_lines")).mappings().all()
    reactions = {}
    for code, iv in interviewers.items():
        result = judge_result(score, iv)
        lines = [r["line"] for r in rows if r["interviewer"] == code and r["result"] == result]
        reactions[code] = random.choice(lines) if lines else ""
    return reactions


def grade_turn(turn: dict, answer: str) -> dict:
    refs = json.loads(turn["source_refs"])
    source_id = refs["source_id"]

    if turn["question_type"] == "EDU":
        chunk = coll.get(ids=[source_id], include=["documents"])
        context = chunk["documents"][0] if chunk["documents"] else ""
        return grade_edu(turn["question"], answer, context)

    # 시사 퀴즈
    score = grade_current(answer, turn["answer_key"])
    art = newscoll.get(ids=[source_id], include=["metadatas"])
    meta = art["metadatas"][0] if art["metadatas"] else {}
    date = datetime.fromtimestamp(meta["pub_ts"]).strftime("%m월 %d일") if meta else ""
    where = f"({meta.get('press', '')}, {date})" if meta else ""
    main_answer = turn["answer_key"].split("|")[0]
    feedback = f"정답이에요! {where}" if score == 100 else f"정답은 '{main_answer}'입니다. {where}"
    return {"score": score, "feedback": feedback.strip(), "model_answer": None, "capped": False}


def submit_answer(session_id: int, user_id: str, turn_id: int, answer: str) -> dict:
    state = get_game_state(session_id)
    check_owner(state, user_id)

    if state["status"] != "PLAYING":
        raise ValueError("이미 끝난 게임입니다")
    if state["remaining_sec"] <= 0:
        raise ValueError("시간이 끝났습니다")

    with get_conn() as conn:
        turn = conn.execute(
            text("""
                SELECT id, interviewer, question_type, question, answer_key, source_refs
                FROM qa_turns
                WHERE id = :t AND session_id = :s AND answer IS NULL AND passed_by_item = 0
            """),
            {"t": turn_id, "s": session_id},
        ).mappings().first()
        if turn is None:
            raise ValueError("답할 수 있는 질문이 아닙니다")
        interviewers = load_interviewers(conn)

    graded = grade_turn(dict(turn), answer)
    score = graded["score"]

    iv = interviewers[turn["interviewer"]]
    result = judge_result(score, iv)
    energy_delta = -iv["fail_minus_score"] if result == "FAIL" else 0
    item = None
    if result == "EXCELLENT" and random.randint(1, 100) <= iv["item_drop_rate"]:
        item = random.choice(ITEMS)

    with get_conn() as conn:
        reactions = pick_reactions(conn, interviewers, score)

        updated = conn.execute(
            text("""
                UPDATE qa_turns
                SET answer = :answer, score = :score, feedback = :feedback,
                    energy_delta = :delta, item_awarded = :item, answered_at = NOW()
                WHERE id = :t AND answer IS NULL
            """),
            {"answer": answer, "score": score, "feedback": graded["feedback"],
             "delta": energy_delta, "item": item, "t": turn_id},
        ).rowcount
        if updated == 0:
            conn.rollback()
            raise ValueError("이미 답한 질문입니다")

        item_sql = f", {ITEM_COLUMNS[item]} = {ITEM_COLUMNS[item]} + 1" if item else ""
        conn.execute(
            text(f"UPDATE game_history SET energy = GREATEST(energy + :delta, 0){item_sql} WHERE id = :s"),
            {"delta": energy_delta, "s": session_id},
        )
        energy = conn.execute(
            text("SELECT energy FROM game_history WHERE id = :s"), {"s": session_id}
        ).scalar()
        conn.commit()

    return {
        "score": score,
        "result": result,
        "feedback": graded["feedback"],
        "model_answer": graded.get("model_answer"),
        "capped": graded.get("capped", False),
        "energy_delta": energy_delta,
        "energy": energy,
        "item": item,
        "reactions": reactions,
        "energy_out": energy <= 0,
    }

def pick_item_speaker(conn, item_type: str, exclude: str | None = None) -> tuple[str, str]:
    rows = conn.execute(
        text("SELECT interviewer, line FROM item_lines WHERE item_type = :t"),
        {"t": item_type},
    ).mappings().all()
    rows = [r for r in rows if r["interviewer"] != exclude]
    r = random.choice(rows)
    return r["interviewer"], r["line"]


def use_item(session_id: int, user_id: str, item_type: str) -> dict:
    if item_type not in ITEM_COLUMNS:
        raise ValueError("없는 아이템입니다")

    state = get_game_state(session_id)
    check_owner(state, user_id)
    if state["status"] != "PLAYING" or state["remaining_sec"] <= 0:
        raise ValueError("진행 중인 게임이 아닙니다")

    col = ITEM_COLUMNS[item_type]

    if item_type == "ENERGY" and state["energy"] >= MAX_ENERGY:
        raise ValueError("에너지가 이미 가득 찼어요")

    with get_conn() as conn:
        pending = get_pending_turn(conn, session_id)
        if item_type == "PASS" and pending is None:
            raise ValueError("넘길 질문이 없습니다")

        # 1. 아이템 개수 차감 (0개면 업데이트 안 됨)
        used = conn.execute(
            text(f"UPDATE game_history SET {col} = {col} - 1 WHERE id = :s AND {col} > 0"),
            {"s": session_id},
        ).rowcount
        if used == 0:
            conn.rollback()
            raise ValueError("아이템이 없습니다")

        # 2. 효과 적용 + 말할 면접관
        if item_type == "PASS":
            speaker, line = pick_item_speaker(conn, "PASS", exclude=pending["interviewer"])
            conn.execute(
                text("UPDATE qa_turns SET passed_by_item = 1, answered_at = NOW() WHERE id = :t"),
                {"t": pending["turn_id"]},
            )
        elif item_type == "TIME":
            speaker, line = pick_item_speaker(conn, "TIME")
            conn.execute(
                text("UPDATE game_history SET ends_at = ends_at + INTERVAL :m MINUTE WHERE id = :s"),
                {"m": TIME_BONUS_MIN, "s": session_id},
            )
        else:  # ENERGY
            speaker, line = pick_item_speaker(conn, "ENERGY")
            conn.execute(
                text("UPDATE game_history SET energy = LEAST(energy + :e, :max) WHERE id = :s"),
                {"e": ENERGY_BONUS, "max": MAX_ENERGY, "s": session_id},
            )

        # 3. 사용 기록
        conn.execute(
            text("""
                INSERT INTO item_uses (session_id, turn_id, item_type, interviewer, line)
                VALUES (:s, :t, :type, :iv, :line)
            """),
            {"s": session_id, "t": pending["turn_id"] if pending else None,
             "type": item_type, "iv": speaker, "line": line},
        )
        conn.commit()

    # 4. 질문 패스면 끼어든 면접관이 바로 다음 질문
    new_question = next_question(session_id, user_id, force_interviewer=speaker) if item_type == "PASS" else None

    return {
        "item": item_type,
        "speaker": speaker,
        "line": line,
        "state": get_game_state(session_id),
        "next_question": new_question,
    }

REASON_LABELS = {
    "TIMEOUT": "시간 종료",
    "NO_ENERGY": "에너지 소진",
    "QUIT": "면접 포기",
    "COMPLETE": "모든 질문 완료",
}

FULL_ENERGY_TURNS = 5   # 이만큼 답해야 에너지 점수를 전부 받음


def score_parts(avg_score: float, energy: int, answered: int, status: str) -> tuple[float, float]:
    """(답변 점수, 에너지 점수)"""
    avg_part = avg_score * 0.7
    if status == "QUIT":
        energy_part = 0.0                                   # 포기하면 에너지 점수 없음
    else:
        ratio = min(1.0, answered / FULL_ENERGY_TURNS)      # 답한 만큼만
        energy_part = energy * 0.3 * ratio
    return round(avg_part, 1), round(energy_part, 1)

def get_result(session_id: int) -> dict:
    with get_conn() as conn:
        g = conn.execute(
            text("""
                SELECT g.*, c.name AS company_name, c.industry, c.pass_score
                FROM game_history g
                JOIN companies c ON c.id = g.company_id
                WHERE g.id = :s
            """),
            {"s": session_id},
        ).mappings().one()

        stats = conn.execute(
            text("""
                SELECT COUNT(*) AS answered, AVG(score) AS avg_score
                FROM qa_turns
                WHERE session_id = :s AND answer IS NOT NULL
            """),
            {"s": session_id},
        ).mappings().one()

    avg_score = float(stats["avg_score"] or 0)
    avg_part, energy_part = score_parts(avg_score, g["energy"], stats["answered"], g["status"])

    return {
        "session_id": g["id"],
        "user_id": g["user_id"],
        "company_name": g["company_name"],
        "industry": g["industry"],
        "status": g["status"],
        "reason": REASON_LABELS.get(g["status"], "진행 중"),
        "answered": stats["answered"],
        "avg_score": round(avg_score, 1),
        "energy": g["energy"],
        "avg_part": avg_part,
        "energy_part": energy_part,
        "final_score": float(g["final_score"]) if g["final_score"] is not None else None,
        "pass_score": g["pass_score"],
        "passed": None if g["passed"] is None else bool(g["passed"]),
        "ended_at": g["ended_at"],
    }


def finish_game(session_id: int, user_id: str, reason: str | None = None) -> dict:
    state = get_game_state(session_id)
    check_owner(state, user_id)

    # 이미 끝난 게임이면 결과만 돌려줌
    if state["status"] != "PLAYING":
        return get_result(session_id)

    # 종료 이유 정하기 (직접 안 주면 상태로 판단)
    if reason is None:
        if state["energy"] <= 0:
            reason = "NO_ENERGY"
        elif state["remaining_sec"] <= 0:
            reason = "TIMEOUT"
        else:
            raise ValueError("아직 진행 중인 게임입니다")
    if reason not in REASON_LABELS:
        raise ValueError("알 수 없는 종료 이유입니다")

    result = get_result(session_id)
    avg_part, energy_part = score_parts(result["avg_score"], result["energy"], result["answered"], reason)
    final_score = round(avg_part + energy_part, 2)
    passed = reason != "QUIT" and final_score >= result["pass_score"]

    with get_conn() as conn:
        conn.execute(
            text("""
                UPDATE game_history
                SET status = :r, ended_at = NOW(), final_score = :f, passed = :p
                WHERE id = :s AND status = 'PLAYING'
            """),
            {"r": reason, "f": final_score, "p": 1 if passed else 0, "s": session_id},
        )
        conn.commit()

    return get_result(session_id)

if __name__ == "__main__":
    sid, uid = 1, "admin"

    r = finish_game(sid, uid, "QUIT")
    for k, v in r.items():
        print(f"  {k}: {v}")

    print("\n한 번 더 호출 (점수 그대로여야 함):")
    print(finish_game(sid, uid)["final_score"])