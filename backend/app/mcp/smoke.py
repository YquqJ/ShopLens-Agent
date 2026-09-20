"""M2 冒烟：langchain-mcp-adapters 加载 5 源，单 SKU 取三源报价 + 故障注入复现。

验收对照（docs/SPEC.md M2）：
- 单条 SKU 从 3 源各取到报价，含 价格/券/物流 三字段；
- 手动关掉 1 源（failrate=1）能复现「源故障」。
"""
import asyncio
import json

from app.mcp import adapter

SKU = "SKU0001"
PLATFORMS = ["jd", "taobao", "pdd"]
# 平台 -> 对应比价 server 名
SERVER_ALIAS = {"jd": "price_jd", "taobao": "price_taobao", "pdd": "price_pdd"}


def _unwrap(result) -> dict:
    """MCP 工具结果可能是 [{'type':'text','text':'<json>'}] / str / dict，统一成 dict。"""
    if (
        isinstance(result, list)
        and result
        and isinstance(result[0], dict)
        and isinstance(result[0].get("text"), str)
    ):
        return json.loads(result[0]["text"])
    if isinstance(result, str):
        return json.loads(result)
    return result


async def _combined_quote(client) -> None:
    """单 SKU 打印三源的价格/券/物流。"""
    print(f"=== SKU {SKU} 三源报价 ===")
    price_tools = {
        p: (await client.get_tools(server_name=SERVER_ALIAS[p]))[0] for p in PLATFORMS
    }
    coupon_tool = (await client.get_tools(server_name="coupon"))[0]
    logistics_tool = (await client.get_tools(server_name="logistics"))[0]

    for p in PLATFORMS:
        q = _unwrap(await price_tools[p].ainvoke({"sku_id": SKU}))
        c = _unwrap(await coupon_tool.ainvoke({"sku_id": SKU, "platform": p}))
        lg = _unwrap(await logistics_tool.ainvoke({"sku_id": SKU, "platform": p}))
        print(json.dumps({
            "platform": p,
            "price": q["price"],
            "coupon": c.get("coupon"),
            "logistics": {"eta_days": lg["eta_days"], "shipping_fee": lg["shipping_fee"]},
        }, ensure_ascii=False))


async def _fault_repro() -> None:
    """关掉 pdd 源（failrate=1），复现源故障。"""
    print("\n=== 故障注入：pdd 源 failrate=1 ===")
    client = adapter.build_client(overrides={"price_pdd": {"SHOPLENS_FAILRATE_PDD": "1"}})
    tool = (await client.get_tools(server_name="price_pdd"))[0]
    try:
        await tool.ainvoke({"sku_id": SKU})
        print("UNEXPECTED：pdd 源未报错")
    except Exception as e:
        print("pdd 源已故障（抛出异常）：", str(e)[:100])


async def main() -> None:
    await _combined_quote(adapter.build_client())
    await _fault_repro()


if __name__ == "__main__":
    asyncio.run(main())