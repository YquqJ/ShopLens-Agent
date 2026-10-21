"""确定性的订单查询解析、检索和消费统计。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select

from app.db.models import Conversation, Order, User
from app.db.session import async_session_factory


@dataclass(frozen=True)
class TimeRange:
    start_at: datetime
    end_at: datetime
    label: str


@dataclass(frozen=True)
class OrderQuery:
    start_at: datetime
    end_at: datetime
    label: str
    keyword: str | None = None
    category: str | None = None
    platform: str | None = None
    query_type: str = "search"
    group_by: str | None = None


_PLATFORM_ALIASES = {
    "京东": "jd", "jd": "jd", "jingdong": "jd",
    "淘宝": "taobao", "淘宝精选": "taobao", "taobao": "taobao",
    "拼多多": "pdd", "pdd": "pdd",
}
_CATEGORY_TERMS = (
    "耳机", "手机", "电脑", "笔记本", "平板", "外套", "卫衣", "牛仔裤",
    "裤子", "鞋", "衬衫", "T恤", "相机", "键盘", "鼠标", "显示器", "手机壳",
)
_DATE_RE = re.compile(r"(?P<year>20\d{2})[年/-](?P<month>\d{1,2})[月/-](?P<day>\d{1,2})日?")
_RANGE_RE = re.compile(
    r"(?P<start>20\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?)\s*(?:至|到|~|～|-)\s*"
    r"(?P<end>20\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?)"
)
# 口语化天数区间：「这两天」「这几天」「近三天」「最近两天」
_SPAN_RE = re.compile(r"(?P<prefix>最近|这|近)\s*(?P<num>[两二三四五六七八九十]|几|\d+)?\s*[天日]")
# 不含具体天数的模糊区间，统一按默认天数理解
_VAGUE_SPANS = ("这阵子", "这段时间", "近段时间")
_CN_NUMBERS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# 无法确定天数时，「这几天」按 3 天理解
_DEFAULT_SPAN_DAYS = 3


def _span_days(raw: str | None) -> int:
    if not raw:
        return _DEFAULT_SPAN_DAYS
    if raw.isdigit():
        return int(raw)
    return _CN_NUMBERS.get(raw, _DEFAULT_SPAN_DAYS)


def _local_midnight(value: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(value, time.min, tzinfo=tz)


def _parse_date(value: str, tz: ZoneInfo) -> date:
    match = _DATE_RE.search(value)
    if not match:
        raise ValueError("无法解析日期")
    return date(int(match["year"]), int(match["month"]), int(match["day"]))


def resolve_time_range(text: str, now: datetime | None = None, timezone: str = "Asia/Shanghai") -> TimeRange:
    """将自然日期转换为应用时区下的半开区间 [start_at, end_at)。"""
    tz = ZoneInfo(timezone)
    supplied_now = now or datetime.now(tz)
    if supplied_now.tzinfo is None:
        supplied_now = supplied_now.replace(tzinfo=tz)
    current = supplied_now.astimezone(tz)
    today = current.date()
    text = (text or "").strip()

    range_match = _RANGE_RE.search(text)
    if range_match:
        start = _parse_date(range_match["start"], tz)
        end = _parse_date(range_match["end"], tz) + timedelta(days=1)
        return TimeRange(_local_midnight(start, tz), _local_midnight(end, tz), "日期范围")
    date_match = _DATE_RE.search(text)
    if date_match:
        start = _parse_date(date_match.group(0), tz)
        return TimeRange(_local_midnight(start, tz), _local_midnight(start + timedelta(days=1), tz), date_match.group(0))

    if "今天" in text:
        return TimeRange(_local_midnight(today, tz), _local_midnight(today + timedelta(days=1), tz), "今天")
    if "昨天" in text:
        day = today - timedelta(days=1)
        return TimeRange(_local_midnight(day, tz), _local_midnight(today, tz), "昨天")
    if "大前天" in text:
        day = today - timedelta(days=3)
        return TimeRange(_local_midnight(day, tz), _local_midnight(day + timedelta(days=1), tz), "大前天")
    if "前天" in text:
        day = today - timedelta(days=2)
        return TimeRange(_local_midnight(day, tz), _local_midnight(day + timedelta(days=1), tz), "前天")
    if "本周" in text or "这周" in text or "这星期" in text:
        this_monday = today - timedelta(days=today.weekday())
        return TimeRange(_local_midnight(this_monday, tz), _local_midnight(today + timedelta(days=1), tz), "本周")
    if "上周" in text:
        this_monday = today - timedelta(days=today.weekday())
        start = this_monday - timedelta(days=7)
        return TimeRange(_local_midnight(start, tz), _local_midnight(this_monday, tz), "上周")
    if "上个月" in text or "上月" in text:
        first = today.replace(day=1)
        previous_last = first - timedelta(days=1)
        previous_first = previous_last.replace(day=1)
        return TimeRange(_local_midnight(previous_first, tz), _local_midnight(first, tz), "上个月")
    if "今年" in text:
        start = today.replace(month=1, day=1)
        end = start.replace(year=start.year + 1)
        return TimeRange(_local_midnight(start, tz), _local_midnight(end, tz), "今年")

    recent = re.search(r"最近\s*(\d+)\s*天", text)
    if recent:
        days = int(recent.group(1))
        if days <= 0:
            raise ValueError("最近天数必须大于 0")
        return TimeRange(current - timedelta(days=days), current, f"最近{days}天")

    # 口语化区间：「这两天」「这几天」「近三天」「最近两天」
    span = _SPAN_RE.search(text)
    if span:
        days = _span_days(span["num"])
        if days <= 0:
            raise ValueError("最近天数必须大于 0")
        if span["prefix"] == "最近":
            return TimeRange(current - timedelta(days=days), current, f"最近{days}天")
        # 「这两天」「这几天」「近五天」按含今天在内的自然日计算
        start = today - timedelta(days=days - 1)
        return TimeRange(
            _local_midnight(start, tz),
            _local_midnight(today + timedelta(days=1), tz),
            f"这{days}天",
        )

    # 「这阵子」「这段时间」等模糊区间按默认天数处理
    if any(marker in text for marker in _VAGUE_SPANS):
        start = today - timedelta(days=_DEFAULT_SPAN_DAYS - 1)
        return TimeRange(
            _local_midnight(start, tz),
            _local_midnight(today + timedelta(days=1), tz),
            f"这{_DEFAULT_SPAN_DAYS}天",
        )

    raise ValueError("时间范围不明确")


def _clean_query_text(text: str) -> str:
    cleaned = _DATE_RE.sub("", text or "")
    cleaned = re.sub(r"最近\s*\d+\s*天", "", cleaned)
    cleaned = _SPAN_RE.sub("", cleaned)
    cleaned = re.sub(r"这阵子|这段时间|近段时间", "", cleaned)
    cleaned = re.sub(r"大前天|昨天|前天|今天|本周|这周|这星期|上周|上个月|上月|今年", "", cleaned)
    cleaned = re.sub(
        r"花了多少钱|多少钱|平均每单多少钱|买了几单|多少单|按(?:品类|平台)统计|统计|"
        r"买过哪些商品|买了哪些商品|买了些什么东西|买过些什么|买些什么|"
        r"哪些东西|哪些商品|些什么东西|些什么|买了什么|买过什么|是什么",
        "",
        cleaned,
    )
    return re.sub(r"\s+", "", cleaned)


_GENERIC_KEYWORD_RE = re.compile(r"^(?:些|什么|哪些|啥|东西|商品|些东西|些什么东西)+$")


def _is_generic_keyword(candidate: str) -> bool:
    """过滤「些什么东西」这类泛化问句残留，避免被当成商品关键词。"""
    return bool(_GENERIC_KEYWORD_RE.match(candidate))


def normalize_order_query(text: str, parsed_time: TimeRange) -> OrderQuery:
    """解析关键词、品类、平台和明细/统计意图，不调用模型。"""
    text = (text or "").strip()
    platform = next((canonical for alias, canonical in _PLATFORM_ALIASES.items() if alias in text.lower()), None)
    category = next((term for term in _CATEGORY_TERMS if term in text), None)
    query_type = "summary" if any(marker in text for marker in ("多少钱", "花了", "几单", "多少单", "平均", "统计")) else "search"
    group_by = "category" if "按品类" in text else "platform" if "按平台" in text else None

    keyword = None
    cleaned = _clean_query_text(text)
    match = re.search(r"(?:买|购买)(?:了|过)?(.+?)(?:花了|多少钱|买了|买过|$)", cleaned)
    if match:
        candidate = match.group(1)
        candidate = re.sub(r"在(?:京东|淘宝精选?|拼多多)", "", candidate)
        candidate = re.sub(r"^(?:了|过)+", "", candidate)
        candidate = re.sub(r"^(?:一个|一件|一款|一台|一部|个)", "", candidate)
        candidate = candidate.strip("，,。！？!? ")
        if (
            candidate
            and candidate not in {category, "什么", "哪些商品", "哪些", "哪些东西", "个", "东西"}
            and not _is_generic_keyword(candidate)
        ):
            keyword = candidate
    if keyword == category:
        keyword = None
    return OrderQuery(
        start_at=parsed_time.start_at,
        end_at=parsed_time.end_at,
        label=parsed_time.label,
        keyword=keyword,
        category=category,
        platform=platform,
        query_type=query_type,
        group_by=group_by,
    )


def _money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def group_orders(orders: Iterable[dict], dimension: str) -> list[dict]:
    totals: dict[str, tuple[int, Decimal]] = {}
    for order in orders:
        key = str(order.get(dimension) or "未知")
        count, total = totals.get(key, (0, Decimal("0")))
        totals[key] = (count + 1, total + _money(order.get("final_price")))
    return [
        {"key": key, "order_count": count, "total_amount": float(total.quantize(Decimal("0.01")))}
        for key, (count, total) in sorted(totals.items(), key=lambda item: (-item[1][0], item[0]))
    ]


def summarize_orders(orders: Iterable[dict], group_by: str | None = None) -> dict:
    items = list(orders)
    total = sum((_money(item.get("final_price")) for item in items), Decimal("0"))
    average = (total / len(items)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if items else Decimal("0.00")
    return {
        "order_count": len(items),
        "total_amount": float(total.quantize(Decimal("0.01"))),
        "average_order_amount": float(average),
        "groups": group_orders(items, group_by) if group_by else [],
    }


def _serialize_query_order(order: Order) -> dict:
    return {
        "order_id": order.id,
        "order_number": getattr(order, "order_number", ""),
        "sku_id": order.sku_id,
        "title": getattr(order, "title_snapshot", "") or "已购商品",
        "category": getattr(order, "category_snapshot", None),
        "url": getattr(order, "source_url", None),
        "image_url": getattr(order, "image_url_snapshot", None),
        "platform": order.platform,
        "final_price": float(order.final_price) if order.final_price is not None else None,
        "status": order.status,
        "created_at": order.created_at.isoformat() if getattr(order, "created_at", None) else None,
    }


async def search_orders(
    start_at: datetime,
    end_at: datetime,
    keyword: str | None = None,
    category: str | None = None,
    platform: str | None = None,
    profile_key: str | None = None,
) -> list[dict]:
    """检索当前本地画像的已下单快照，按下单时间倒序。"""
    stmt = (
        select(Order)
        .join(Conversation, Order.conversation_id == Conversation.id)
        .join(User, Conversation.user_id == User.id)
        .where(
            Order.status == "placed",
            Order.created_at >= start_at,
            Order.created_at < end_at,
        )
        .order_by(Order.created_at.desc())
    )
    if profile_key:
        stmt = stmt.where(User.external_id == profile_key)
    if platform:
        stmt = stmt.where(Order.platform == platform)
    if keyword:
        stmt = stmt.where(Order.title_snapshot.ilike(f"%{keyword}%"))
    if category:
        stmt = stmt.where(or_(
            Order.category_snapshot == category,
            Order.title_snapshot.ilike(f"%{category}%"),
        ))
    async with async_session_factory() as session:
        result = await session.execute(stmt)
        return [_serialize_query_order(order) for order in result.scalars().all()]


async def search_orders_page(
    *,
    page: int = 1,
    page_size: int = 20,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    keyword: str | None = None,
    category: str | None = None,
    profile_key: str | None = None,
) -> dict:
    """检索订单列表并返回分页元数据和当前画像可用的品类。"""
    filters = [Order.status == "placed"]
    if start_at is not None:
        filters.append(Order.created_at >= start_at)
    if end_at is not None:
        filters.append(Order.created_at < end_at)
    if profile_key:
        filters.append(User.external_id == profile_key)
    if keyword:
        filters.append(Order.title_snapshot.ilike(f"%{keyword}%"))
    if category:
        filters.append(or_(
            Order.category_snapshot == category,
            Order.title_snapshot.ilike(f"%{category}%"),
        ))

    orders_stmt = (
        select(Order)
        .join(Conversation, Order.conversation_id == Conversation.id)
        .join(User, Conversation.user_id == User.id)
        .where(*filters)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    count_stmt = (
        select(func.count(Order.id))
        .join(Conversation, Order.conversation_id == Conversation.id)
        .join(User, Conversation.user_id == User.id)
        .where(*filters)
    )
    category_filters = [Order.status == "placed", Order.category_snapshot.is_not(None)]
    if profile_key:
        category_filters.append(User.external_id == profile_key)
    categories_stmt = (
        select(Order.category_snapshot)
        .join(Conversation, Order.conversation_id == Conversation.id)
        .join(User, Conversation.user_id == User.id)
        .where(*category_filters)
        .distinct()
        .order_by(Order.category_snapshot)
    )

    async with async_session_factory() as session:
        result = await session.execute(orders_stmt)
        total = int((await session.scalar(count_stmt)) or 0)
        categories = [value for value in (await session.scalars(categories_stmt)).all() if value]
        return {
            "items": [_serialize_query_order(order) for order in result.scalars().all()],
            "total": total,
            "page": page,
            "page_size": page_size,
            "categories": categories,
        }

