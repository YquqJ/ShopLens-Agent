"""调试端点（演示三态熔断用，非生产功能）：故障注入开关 + 熔断器三态查询。

设计决策：
- mock 源是 stdio 子进程，运行中改后端进程 env 传不进去，故用「覆盖标记文件」做跨进程运行时注入——
  本端点写 backend/.chaos/<source>，子进程 should_fail 每次调用前读该文件（见 mcp/_sim.py）；
- 三态查询读 app.state.source_registry（main.py lifespan 注入），返回每源 closed/open/half_open。
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.mcp._sim import clear_override, override_file, set_override

router = APIRouter()

# mock 比价源（供前端开关枚举；熔断演示聚焦三个比价源，不含 coupon/logistics）
SOURCES = ["jd", "taobao", "pdd"]


class FailRateRequest(BaseModel):
    source: str = Field(..., description="jd / taobao / pdd")
    rate: float = Field(..., ge=0, le=1, description="故障概率 0~1；传 0 表示清除注入")


@router.post("/debug/failrate")
async def set_failrate(req: FailRateRequest) -> dict:
    if req.rate > 0:
        set_override(req.source, req.rate)
    else:
        clear_override(req.source)
    return {"source": req.source, "rate": req.rate}


@router.get("/debug/failrate")
async def get_failrate() -> dict:
    """当前注入状态（含未注入源），供前端加载时同步开关。"""
    out = {}
    for src in SOURCES:
        f = override_file(src)
        out[src] = float(f.read_text(encoding="utf-8").strip()) if f.exists() else 0.0
    return {"sources": out}


@router.get("/debug/breakers")
async def breaker_states(request: Request) -> dict:
    registry = getattr(request.app.state, "source_registry", None)
    if registry is None:
        return {"sources": {}}
    return {"sources": registry.breaker_states()}