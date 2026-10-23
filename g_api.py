import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import text

from db import get_conn
from g_auth import get_current_user
from g_load_doc import coll, register_document

from pydantic import BaseModel
from sqlalchemy.exc import NoResultFound

from g_game import (
    check_owner, finish_game, get_game_state, get_result,
    next_question, start_game, submit_answer, use_item,
)

router = APIRouter(prefix="/api")

ALLOWED_EXT = {".pdf", ".txt"}
MAX_SIZE = 20 * 1024 * 1024   # 20MB

DOC_COLUMNS = "id, name, file_name, chunk_count, status, created_at"


# ---------- 내 자료 목록 ----------
@router.get("/docs")
def list_docs(user_id: str = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            text(f"""
                SELECT {DOC_COLUMNS} FROM doc_upload
                WHERE user_id = :u AND status <> 'DELETED'
                ORDER BY id DESC
            """),
            {"u": user_id},
        ).mappings().all()
    return [dict(r) for r in rows]


# ---------- 자료 올리기 ----------
@router.post("/docs")
def upload_doc(
    name: str = Form(""),
    file: UploadFile | None = File(None),
    user_id: str = Depends(get_current_user),
):
    name = name.strip()
    if not name or len(name) > 100:
        raise HTTPException(400, "자료 이름을 1~100자로 입력해 주세요")
    if file is None or not file.filename:
        raise HTTPException(400, "파일을 선택해 주세요")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "PDF 또는 TXT 파일만 올릴 수 있어요")

    # 임시 폴더에 받은 뒤 register_document로 넘김 (끝나면 임시 폴더는 자동 삭제)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / Path(file.filename).name
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        if tmp_path.stat().st_size > MAX_SIZE:
            raise HTTPException(400, "파일은 20MB까지 올릴 수 있어요")

        doc_id = register_document(user_id, name, str(tmp_path))

    with get_conn() as conn:
        row = conn.execute(
            text(f"SELECT {DOC_COLUMNS} FROM doc_upload WHERE id = :id"),
            {"id": doc_id},
        ).mappings().one()
    return dict(row)


# ---------- 자료 삭제 ----------
@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: int, user_id: str = Depends(get_current_user)):
    with get_conn() as conn:
        row = conn.execute(
            text("SELECT file_path FROM doc_upload WHERE id = :id AND user_id = :u AND status <> 'DELETED'"),
            {"id": doc_id, "u": user_id},
        ).mappings().first()
        if row is None:
            raise HTTPException(404, "자료를 찾을 수 없어요")

        conn.execute(text("UPDATE doc_upload SET status = 'DELETED' WHERE id = :id"), {"id": doc_id})
        conn.commit()

    coll.delete(where={"document_id": doc_id})        # Chroma 청크 삭제
    Path(row["file_path"]).unlink(missing_ok=True)     # 저장된 파일 삭제
    return {"success": True}

# 게임 API

class StartReq(BaseModel):
    document_ids: list[int]
    include_current: bool = False
    minutes: int = 20


class AnswerReq(BaseModel):
    turn_id: int
    answer: str


class ItemReq(BaseModel):
    item_type: str   # PASS / TIME / ENERGY


def call(fn, *args, **kwargs):
    """g_game 함수의 파이썬 예외를 HTTP 응답으로 바꿈"""
    try:
        return fn(*args, **kwargs)
    except NoResultFound:
        raise HTTPException(404, "게임을 찾을 수 없어요")
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


def my_state(session_id: int, user_id: str) -> dict:
    """게임 상태를 가져오면서 본인 게임인지 확인"""
    state = call(get_game_state, session_id)
    call(check_owner, state, user_id)
    return state


def finish_if_over(session_id: int, user_id: str) -> dict | None:
    """끝났거나 끝나야 하면 결과를, 아직 진행 중이면 None"""
    state = my_state(session_id, user_id)
    if state["status"] != "PLAYING":
        return get_result(session_id)
    if state["energy"] <= 0 or state["remaining_sec"] <= 0:
        return call(finish_game, session_id, user_id)
    return None


# ---------- 게임 시작 ----------
@router.post("/game/start")
def api_start(req: StartReq, user_id: str = Depends(get_current_user)):
    sid = call(start_game, user_id, req.document_ids, req.include_current, req.minutes)
    return {"session_id": sid, "state": get_game_state(sid)}


# ---------- 현재 상태 ----------
@router.get("/game/{session_id}")
def api_state(session_id: int, user_id: str = Depends(get_current_user)):
    return my_state(session_id, user_id)


# ---------- 다음 질문 ----------
@router.post("/game/{session_id}/next")
def api_next(session_id: int, user_id: str = Depends(get_current_user)):
    result = finish_if_over(session_id, user_id)
    if result:
        return {"finished": True, "result": result}

    q = call(next_question, session_id, user_id)
    if q is None:   # 낼 질문이 없음 → 조기 종료
        return {"finished": True, "result": call(finish_game, session_id, user_id, "COMPLETE")}

    return {"finished": False, "question": q, "state": get_game_state(session_id)}


# ---------- 답변 제출 ----------
@router.post("/game/{session_id}/answer")
def api_answer(session_id: int, req: AnswerReq, user_id: str = Depends(get_current_user)):
    result = finish_if_over(session_id, user_id)
    if result:   # 답하는 사이에 시간이 끝난 경우
        return {"finished": True, "result": result}

    r = call(submit_answer, session_id, user_id, req.turn_id, req.answer)
    if r["energy_out"]:
        return {**r, "finished": True, "result": call(finish_game, session_id, user_id)}

    return {**r, "finished": False, "state": get_game_state(session_id)}


# ---------- 아이템 사용 ----------
@router.post("/game/{session_id}/item")
def api_item(session_id: int, req: ItemReq, user_id: str = Depends(get_current_user)):
    return call(use_item, session_id, user_id, req.item_type)


# ---------- 포기 ----------
@router.post("/game/{session_id}/quit")
def api_quit(session_id: int, user_id: str = Depends(get_current_user)):
    return call(finish_game, session_id, user_id, "QUIT")


# ---------- 결과 ----------
@router.get("/game/{session_id}/result")
def api_result(session_id: int, user_id: str = Depends(get_current_user)):
    result = finish_if_over(session_id, user_id)
    if result is None:
        raise HTTPException(400, "아직 진행 중인 게임이에요")
    return result


# ---------- 내 면접 기록 ----------
@router.get("/games")
def api_my_games(user_id: str = Depends(get_current_user)):
    with get_conn() as conn:
        rows = conn.execute(
            text("""
                SELECT g.id, c.name AS company_name, c.industry, c.pass_score,
                       g.status, g.final_score, g.passed, g.started_at
                FROM game_history g
                JOIN companies c ON c.id = g.company_id
                WHERE g.user_id = :u
                ORDER BY g.started_at DESC
                LIMIT 20
            """),
            {"u": user_id},
        ).mappings().all()
    return [dict(r) for r in rows]

# ---------- 면접 복기 (질문·답변 목록) ----------
@router.get("/game/{session_id}/turns")
def api_turns(session_id: int, user_id: str = Depends(get_current_user)):
    my_state(session_id, user_id)   # 본인 게임인지 확인
    with get_conn() as conn:
        rows = conn.execute(
            text("""
                SELECT turn_no, interviewer, question_type, question,
                       answer, score, feedback, passed_by_item
                FROM qa_turns
                WHERE session_id = :s
                ORDER BY turn_no
            """),
            {"s": session_id},
        ).mappings().all()
    return [dict(r) for r in rows]