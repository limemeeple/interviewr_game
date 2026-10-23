from typing import Dict
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import RedirectResponse
from starlette.staticfiles import StaticFiles

from bcrypt_utils import get_token
from db import get_conn
from fastapi import Depends
from g_auth import get_current_user
from g_api import router as api_router

app = FastAPI()
app.include_router(api_router)
app.mount("/view", StaticFiles(directory="view"))

# 메인페이지(로그인페이지)
@app.get("/")
def main():
    return RedirectResponse("/view/login.html")

# 로그인
@app.post("/login")
def login(info:Dict[str,str], req:Request):

    # sucess : 성공여부(아이디 존재 여부)
    # token : jwt 토큰 발급
    # message : 로그인 또는 로그인 실패시 노출할 메시지
    json = {'sucess':'N', 'token':'', 'message':''}

    conn = get_conn()

    # 아이디와 비번을 가지고 데이터 조회한다.
    # 데이터가 조회(count = 1)일 경우 존재하는 아이디로 판단
    # nick(별명)의 경우 로그인 시 환영 문구에 넣기위해 가져옴.
    sql = text("SELECT COUNT(*) AS cnt, MAX(name) AS name FROM users WHERE id = :id AND pw = SHA2(:pw, 256)")

    try:
        result = conn.execute(sql, {"id":info['id'], "pw":info['pw']}).mappings().fetchone()
        print(f'----  result : {result.cnt}')

        check_cnt = int(result.cnt)

        if check_cnt >= 1:
            token = get_token({"id":info["id"], "ip":req.client.host})
            json.update({'success': 'Y', 'token': token, 'message': f'{result.name}님 환영합니다!'})
        else :
            json.update({'success': 'N', 'token': '', 'message': f'아이디 또는 비밀번호를 확인해주세요.'})
    except Exception as e:
        print(f"### 로그인 에러: {e}")
        json.update({'success': 'N', 'token': '', 'message': '로그인에 실패하였습니다.'})

    finally:
        conn.close()

    return json

# 아이디 중복 체크
@app.post("/idChk")
def id_chk(info:Dict[str,str], req:Request):
    json = {'chkYN':''}

    conn = get_conn()

    # 아이디를 개수를 체크하여 중복 아이디가 있는지 확인한다.
    sql = text("SELECT COUNT(*) AS cnt FROM users WHERE id = :id")
    try:
        result = conn.execute(sql, {"id":info['id']}).mappings().fetchone()
        print(f'----  result : {result.cnt}')
        check_cnt = int(result.cnt)

        # 혹시 몰라서 1개 이상으로 처리함.
        # 1개 이상 검증 됐을 시 'N' 사용 불가능한 id라고 전달.
        if check_cnt >= 1:
            json.update({'chkYN' : 'N'})
        # 그 외에는 사용 가능한 id라고 'Y' 전달.
        else :
            json.update({'chkYN' : 'Y'})

    except Exception as e:
        # 에러 일 경우는 E코드로 줘서 화면단에서 에러발생했다고 처리하려고 함.
        json.update({'chkYN' : 'E'})

    finally:
        conn.close()

    return json

# 가입하기
@app.post("/registSave")
def regist_save(info:Dict[str,str], req:Request):
    json = {'success':'N', 'message':''}

    conn = get_conn()

    # 아이디를 개수를 체크하여 중복 아이디가 있는지 확인한다.
    sql = text("INSERT INTO users (id, pw, name, email) VALUES (:id, SHA2(:pw, 256), :name, :email)")

    try:
        result = conn.execute(sql, {"id": info['id'], "pw": info['pw'], "name": info['name'], "email": info['email']})
        print(f'----  result : {result.rowcount}')

        conn.commit()

        json.update({'success': 'Y', 'message': '가입에 성공하였습니다.'})

    except Exception as e:
        print(f"### Exception : {e}")
        conn.rollback()
        json.update({'success':'N', 'message':'저장에 실패하였습니다.'})

    finally:
        conn.close()

    return json

@app.get("/api/me")
def me(user_id: str = Depends(get_current_user)):
    return {"id": user_id}