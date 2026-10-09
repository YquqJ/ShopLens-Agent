# ShopLens Agent

ShopLens 是一个面向电商售前场景的 **LangGraph 单 Agent 导购系统**。它将用户的自然语言需求逐步收敛为结构化偏好，检索候选商品，通过 MCP 获取多平台报价、优惠券和物流信息，计算综合成本，并在人工确认和下单前锁价后完成交易闭环。

项目重点不是简单调用 LLM，而是验证 Agent 在真实业务链路中的 **状态编排、外部工具接入、事实约束、可靠性治理和可观测性**。

## 核心能力

- **单 Agent 交易编排**：基于 LangGraph StateGraph 串联意图路由、需求澄清、商品检索、跨平台比价、推荐、确认、锁价和下单。
- **多轮需求结构化**：从自然语言提取品类、预算、颜色、风格、材质、场景等字段，支持跨轮补全，并区分本轮条件与跨会话偏好。
- **MCP 多源工具调用**：通过 FastMCP 提供京东、淘宝、拼多多仿真报价，以及优惠券、物流和可选真实商品检索源；由 `langchain-mcp-adapters` 加载为 LangChain 工具。
- **事实约束与可解释推荐**：价格、库存、平台、商品属性来自数据库和工具，LLM 只生成推荐理由；推荐结果保留商品属性和来源信息。
- **HITL 与价格锁定**：下单前暂停图执行，用户确认后通过 `Command(resume=...)` 恢复；二次询价发现涨价时重新确认，避免展示价与下单价不一致。
- **可靠性治理**：每个 MCP 数据源独立使用指数退避重试、三态熔断和降级策略，故障源不会阻塞其他数据源。
- **全链路观测**：OpenTelemetry 记录工具调用 Trace，Langfuse 关联 LangGraph、LLM 和 MCP 调用，支持按会话和节点排查问题。

## 系统架构

```text
用户请求
   |
   v
load_memory -> route -> clarify -> retrieve
                                  |
                                  v
              compare(MCP) -> normalize -> recommend
                                                |
                                                v
                              confirm(interrupt) -> lock_price -> place_order
                                      ^                    |
                                      |                    v
                                      +---- Command(resume)

MCP 数据源:
  price_jd / price_taobao / price_pdd  多平台报价
  coupon                              优惠券
  logistics                           物流与运费
  zhidemai                            可选真实商品检索源

事实数据:
  PostgreSQL  商品、库存、会话、画像、订单和检查点
  Chroma      商品向量检索
```


## 技术栈

| 层次 | 技术 |
| --- | --- |
| Agent 编排 | Python 3.12、LangGraph、LangChain、Qwen |
| Web 服务 | FastAPI、SSE、Pydantic |
| 数据存储 | PostgreSQL 16、SQLAlchemy 2.0、Chroma |
| 工具协议 | MCP、FastMCP、langchain-mcp-adapters |
| 可靠性 | tenacity、手写三态熔断器、降级策略 |
| 可观测性 | OpenTelemetry、Langfuse |
| 前端 | Vue 3、TypeScript、Vite、Pinia、ECharts |
| 环境 | uv、Docker Compose、pnpm |

## 项目结构

```text
ShopLens-Agent/
├── backend/
│   ├── app/
│   │   ├── agents/       # LangGraph StateGraph、状态和节点
│   │   ├── api/          # FastAPI REST、SSE、resume 接口
│   │   ├── db/           # PostgreSQL 与 Chroma
│   │   ├── eval/         # 黄金集、任务完成率、Token、延迟、混沌评测
│   │   ├── infra/        # 重试、熔断、降级、OTel、Langfuse
│   │   ├── mcp/          # FastMCP server 与 MCP client 装配
│   │   ├── services/     # 商品、归一、订单、画像等业务服务
│   │   └── tools/        # 应用内业务工具
│   ├── pyproject.toml
│   └── tests/
├── frontend/             # Vue 3 + Vite 前端
├── docs/                 # SPEC、设计决策和评测数字台账
├── docker-compose.yml
├── .env.example
└── README.md
```

## 快速开始

### 1. 准备环境变量

```powershell
Copy-Item .env.example .env
```

至少填写 `.env` 中的 `DASHSCOPE_API_KEY`。`docker-compose.yml` 将 PostgreSQL 映射到宿主机 `15434` 端口，因此本地运行时建议使用：

```env
DATABASE_URL=postgresql+psycopg://shoplens:shoplens@localhost:15434/shoplens
```

Langfuse 默认关闭。需要观测时再填写 `LANGFUSE_ENABLED`、公钥、私钥和服务地址。

### 2. 启动 PostgreSQL

```powershell
docker compose up -d
```

### 3. 安装后端依赖并初始化商品数据

```powershell
cd backend
uv sync
uv run python -m app.services.catalog
```

该命令会创建 PostgreSQL 表，并将种子商品写入 PostgreSQL 和 Chroma。

### 4. 启动后端

```powershell
uv run python -m app.run
```

后端默认地址：`http://127.0.0.1:8000`。

### 5. 启动前端

另开终端执行：

```powershell
cd frontend
pnpm install
pnpm dev
```

前端默认地址：`http://localhost:5173`。Vite 会将 `/api` 请求代理到后端 `8000` 端口。

## 数据源模式

默认使用本地仿真数据源，便于离线开发和故障注入。前端可以在新一轮会话中切换数据源模式：

- `mock`：京东、淘宝、拼多多仿真报价，附带优惠券和物流数据，可用于完整比价、锁价和下单演示。
- `real`：启用值得买真实商品检索源，需要配置 `ZDM_API_KEY` 和 `SHOPLENS_ZHIDEMAI_ENABLED=1`；真实模式用于商品检索，不进入仿真下单闭环。

MCP server 使用 stdio 子进程运行，每个数据源有独立进程和持久化 session。应用启动时由 MCP client 建立 session、加载工具，再交给 LangGraph 节点调用。

## 评测与测试

运行单元测试：

```powershell
cd backend
uv run pytest
```

主要评测脚本：

```powershell
# 跨平台报价归一与最优解黄金集
uv run python -m app.eval.judge

# 澄清轮次与收敛情况
uv run python -m app.eval.clarify

# MCP 单源故障、重试、熔断和降级
uv run python -m app.eval.chaos

# 多源比价串行/并发 A/B 基准
uv run python -m app.eval.compare_concurrency

# 推荐事实卡片 TTFT（输入到首卡 / 输入到完整推荐）
uv run python -m app.eval.recommend_ttft

# 真实 StateGraph 节点和端到端延迟
uv run python -m app.eval.e2e_latency

# Token 基线与上下文裁剪对照
uv run python -m app.eval.token_profiler
```

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/chat` | SSE 流式聊天，支持 `source_mode=mock\|real` |
| `POST` | `/api/resume` | 恢复 HITL 确认后的图执行 |
| `GET` | `/api/conversations` | 会话列表 |
| `GET` | `/api/profile/preferences` | 读取跨会话偏好 |
| `POST` | `/api/select-product` | 真实源推荐结果确认 |
| `GET` | `/api/funnel` | 转化漏斗统计 |
| `GET` | `/api/debug/breakers` | 查询 MCP 熔断器状态 |

## 注意事项

- `.env`、Langfuse 密钥和数据库数据不应提交到仓库。
- 仿真 MCP 源用于开发、演示和可靠性测试，不代表真实平台生产接口。
- `real` 模式依赖外部 API Key，且不支持仿真下单闭环。
- 端到端下单评测必须使用隔离数据库，并在测试完成后清理评测数据。
