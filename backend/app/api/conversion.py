"""转化事件 REST：前端埋点写入 + 漏斗统计（M8 转化看板）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import conversion

router = APIRouter()


class EventRequest(BaseModel):
    thread_id: str
    event_type: str  # impression / click / order
    sku_id: str | None = None


@router.post("/conversion")
async def post_conversion(req: EventRequest) -> dict:
    try:
        return await conversion.record_event(req.thread_id, req.event_type, req.sku_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.get("/funnel")
async def get_funnel() -> dict:
    return await conversion.funnel_stats()