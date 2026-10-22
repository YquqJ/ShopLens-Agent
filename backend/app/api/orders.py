"""订单列表 REST API。"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from app.services import order_query as order_query_service

router = APIRouter()
APP_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _parse_date(value: str | None, label: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{label}格式应为 YYYY-MM-DD") from exc
    return datetime.combine(parsed, time.min, tzinfo=APP_TIMEZONE)


@router.get("/orders")
async def list_orders(
    profile_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    start_date: str | None = None,
    end_date: str | None = None,
    keyword: str | None = None,
    category: str | None = None,
) -> dict:
    start_at = _parse_date(start_date, "开始日期")
    end_at = _parse_date(end_date, "结束日期")
    if start_at is not None and end_at is not None and start_at > end_at:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期")
    if end_at is not None:
        end_at += timedelta(days=1)
    return await order_query_service.search_orders_page(
        profile_key=profile_id,
        page=page,
        page_size=page_size,
        start_at=start_at,
        end_at=end_at,
        keyword=keyword.strip() or None if keyword else None,
        category=category.strip() or None if category else None,
    )
