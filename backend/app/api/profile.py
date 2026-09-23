"""浏览器画像读取接口：无会话时也能恢复已授权的长期偏好。"""
from fastapi import APIRouter

from app.services import profile

router = APIRouter()


@router.get("/profile/preferences")
async def get_profile_preferences(profile_id: str) -> dict:
    return {"profile_preferences": await profile.get_preferences(profile_id)}
