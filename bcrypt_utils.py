import datetime
import os
from typing import Any

import jwt
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET")
if not SECRET_KEY:
    print(".env에 JWT_SECRET이 없습니다")

ALGORITHM = "HS256"
TOKEN_EXPIRE_MIN = 180


def get_token(data: dict[str, Any]) -> str:
    payload = dict(data)
    payload["exp"] = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=TOKEN_EXPIRE_MIN)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None