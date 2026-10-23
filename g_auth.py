from fastapi import Header, HTTPException, Request

from bcrypt_utils import verify_token


def get_current_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")

    payload = verify_token(authorization.removeprefix("Bearer "))
    if payload is None:
        raise HTTPException(status_code=401, detail="로그인이 만료되었습니다. 다시 로그인해 주세요")

    if payload.get("ip") != request.client.host:
        raise HTTPException(status_code=401, detail="다른 곳에서 발급된 로그인 정보입니다")

    return payload["id"]