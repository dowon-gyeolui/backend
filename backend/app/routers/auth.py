from fastapi import APIRouter

router = APIRouter()


@router.get("/kakao")
async def kakao_login():
    # TODO: Redirect to Kakao OAuth authorization URL
    return {"message": "Kakao login — not implemented yet"}


@router.get("/kakao/callback")
async def kakao_callback(code: str):
    # TODO: Exchange code for Kakao access token, fetch user profile, issue JWT
    return {"message": "Kakao callback — not implemented yet", "code": code}
