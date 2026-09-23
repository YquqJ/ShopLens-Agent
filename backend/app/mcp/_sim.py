"""MCP 仿真源共享逻辑：确定性报价、随机波动、故障注入。

设计决策：
- 三个比价源（jd/taobao/pdd）逻辑同构，抽到本模块避免重复；
- 报价 = sku_id 确定性市场价 × 平台系数 × 随机波动，不读 Postgres——
  模拟「第三方平台独立定价」，第三方源只读、无我方主库依赖；
  （平台系数：jd 1.02 略贵、pdd 0.93 最便宜，贴近真实平台调性）
- 故障注入走环境变量 SHOPLENS_FAILRATE_<SOURCE>（0~1 概率），
  便于 M2 验收「关掉 1 源复现源故障」，也复用于 M5 chaos / M6 熔断。
"""
import os
import random
from pathlib import Path

# 平台相对市场价的系数
PLATFORM_FACTOR = {"jd": 1.02, "taobao": 0.98, "pdd": 0.93}

# 饰品市场价区间（元）
_LOW, _HIGH = 300.0, 5000.0


def rng_for(*keys) -> random.Random:
    """由任意键稳定生成 Random（确定性，不依赖 PYTHONHASHSEED 与进程环境）。"""
    n = 0
    for key in keys:
        for ch in str(key):
            n = (n * 131 + ord(ch)) & 0xFFFFFFFF
    return random.Random(n)


def market_price(sku_id: str) -> float:
    """由 sku_id 稳定映射一个市场价（同一商品跨平台一致）。"""
    return round(rng_for(sku_id).uniform(_LOW, _HIGH), 2)


def quote(sku_id: str, platform: str) -> float:
    """一次报价：市场价 × 平台系数 × (0.98~1.02 随机波动)。"""
    price = market_price(sku_id) * PLATFORM_FACTOR[platform] * random.uniform(0.98, 1.02)
    return round(price, 2)


# 运行时故障注入覆盖目录：前端调试开关写这里，子进程 should_fail 每次调用前读。
# 子进程与后端共享磁盘，故走后端进程写文件 → 子进程即刻生效（env 只在 spawn 时传入，运行中改不到）。
CHAOS_DIR = Path(__file__).resolve().parents[2] / ".chaos"


def override_file(source: str) -> Path:
    return CHAOS_DIR / source.lower()


def set_override(source: str, rate: float) -> None:
    """写覆盖文件（调试端点/front 调用），使该 mock 源按 rate 概率注入故障。"""
    CHAOS_DIR.mkdir(parents=True, exist_ok=True)
    override_file(source).write_text(str(rate), encoding="utf-8")


def clear_override(source: str) -> None:
    """清除覆盖，退回环境变量默认故障率。"""
    f = override_file(source)
    if f.exists():
        f.unlink()


def should_fail(source: str) -> bool:
    """按注入故障：运行时覆盖文件优先，否则环境变量 SHOPLENS_FAILRATE_<SOURCE>=0~1，命中即应抛错。"""
    raw = os.environ.get(f"SHOPLENS_FAILRATE_{source.upper()}", "0")
    f = override_file(source)
    if f.exists():
        try:
            raw = f.read_text(encoding="utf-8").strip()
        except OSError:
            raw = os.environ.get(f"SHOPLENS_FAILRATE_{source.upper()}", "0")
    try:
        rate = float(raw)
    except ValueError:
        rate = 0.0
    return random.random() < rate