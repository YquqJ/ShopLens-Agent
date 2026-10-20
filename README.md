# ShopLens Agent

ShopLens 是面向电商售前场景的 LangGraph 单 Agent。系统把自然语言需求结构化，通过值得买官方 MCP 检索真实商品，由 LLM 基于真实源字段生成候选对比决策；用户选择并确认后，系统将模拟订单写入 PostgreSQL，并支持继续查询订单和消费统计。

## 核心能力

- 单 Agent StateGraph：意图路由、需求澄清、商品检索、事实约束推荐、人工确认和模拟下单。
- 值得买真实 MCP：通过 `langchain-mcp-adapters` 动态发现官方商品检索工具，不在业务代码中直连外部 HTTP。
- 证据约束推荐：商品价格、商城、属性和价格标签来自值得买真实源，LLM 只负责比较取舍和推荐理由。
- 可靠性治理：MCP 调用统一使用指数退避、`CLOSED/OPEN/HALF_OPEN` 三态熔断、降级和 OpenTelemetry/Langfuse Trace。
- 自然语言订单查询：支持“昨天我买了什么”“上个月买耳机花了多少钱”等明细查询和消费统计。

## 架构

```text
用户输入
   |
   v
load_memory -> route -> clarify -> retrieve(值得买 MCP)
                                      |
                                      v
                               recommend
                                      |
                                      v
                           confirm(interrupt)
                                      |
                                      v
                              place_order

订单查询：route -> 时间/条件解析 -> 订单检索/聚合 MCP -> 订单卡片或统计摘要
```

## 技术栈

| 层次 | 技术 |
| --- | --- |
| Agent 编排 | Python 3.12、LangGraph、LangChain、Qwen |
| Web 服务 | FastAPI、SSE、Pydantic |
| 数据存储 | PostgreSQL 16、SQLAlchemy 2.0 |
| 工具协议 | MCP、FastMCP、langchain-mcp-adapters |
| 可靠性 | tenacity、三态熔断器、OpenTelemetry、Langfuse |
| 前端 | Vue 3、TypeScript、Vite、Pinia、ECharts |
| 环境 | uv、Docker Compose、pnpm |

## 快速开始

```powershell
Copy-Item .env.example .env
docker compose up -d
cd backend
uv sync
uv run python -m app.run
```

在项目根目录 `.env` 中配置：

```env
DASHSCOPE_API_KEY=
ZDM_API_KEY=
DATABASE_URL=postgresql+psycopg://shoplens:shoplens@localhost:15434/shoplens
```

另开终端启动前端：

```powershell
cd frontend
pnpm install
pnpm dev
```

前端地址：`http://localhost:5173`；后端地址：`http://127.0.0.1:8000`。

## 目录说明

```text
backend/app/agents/       LangGraph 状态、节点和单 Agent 编排
backend/app/api/          FastAPI、SSE、会话、订单和转化接口
backend/app/db/           PostgreSQL ORM 与会话
backend/app/infra/        重试、熔断、观测
backend/app/mcp/          值得买 MCP 客户端适配
backend/app/services/     订单、画像、历史、转化业务服务
backend/app/eval/         澄清、记忆、任务完成率评测
frontend/                 Vue 3 客户端
docs/SPEC.md              当前施工规格
```

## 测试

```powershell
cd backend
uv run pytest
```

真实 MCP smoke/integration 测试需要配置 `ZDM_API_KEY`，离线单测不访问外部商品源。

## 约束

- 当前只面向一个本地用户，不提供多用户隔离。
- 不支持按订单号查询；订单查询使用自然语言时间、关键词、品类和平台条件。
- 下单仅为模拟交易，不调用真实商城支付或库存接口。
- `docs/SPEC.md` 是当前功能范围和验收标准的唯一事实来源。
