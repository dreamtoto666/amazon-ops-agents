# Listing Agent MCP 接入接口

## 1. 接口目标

Listing Agent 通过统一边界接入卖家精灵 MCP 和 Sif MCP。业务图只依赖类型化端口，不直接依赖具体 SDK、HTTP 实现或密钥。

领星 MCP 也复用同一 Streamable HTTP 传输层，使用 `X-Mcp-Key` 认证。其顶层只暴露 `help`、`search`、`action`：消费者先发现业务工具和 Schema，再通过 `action` 执行调用。广告巡检仅允许静态白名单中的只读业务工具；创建监控、修改广告等写工具保持禁用。目录结果按业务工具和版本缓存，遇到工具或版本失效时仅刷新一次后重试只读请求。领星工具将在销售利润、广告、库存和市场风险 Agent 分别实现字段映射后再接入总控路由。

```text
Listing Agent
    ↓
KeywordResearchGateway
    ↓
MCPProviderClient
    ↓
StreamableHTTPMCPTransport
    ↓
卖家精灵 MCP / Sif MCP
```

## 2. 已提供能力

- 服务地址和密钥环境变量配置；
- 工具目录发现；
- 每个服务独立的工具白名单；
- 标准化工具调用结果和调用 ID；
- 超时、连接错误和不可重试错误分类；
- 调用耗时记录；
- 卖家精灵关键词工具的类型化方法；
- Sif 关键词工具端口；
- 官方 MCP Python SDK 的 Streamable HTTP Transport；
- 跨数据源有界并发和单源失败降级；
- 两种返回格式到 `KeywordEvidence` 的确定性映射；
- 查询指纹、调用摘要、原始证据引用和警告；
- 密钥不进入 LangGraph State、事件或返回结果。

## 3. 服务配置

卖家精灵：

```text
endpoint: https://mcp.sellersprite.com/mcp
transport: streamable HTTP
auth header: secret-key
secret env: SELLER_SPRITE_MCP_SECRET
```

Sif：

```text
endpoint: https://mcp.sif.com/mcp
transport: streamable HTTP
auth header: secret-key
secret env: SIF_MCP_SECRET
```

## 4. Transport 实现

已使用官方 MCP Python SDK 实现 `StreamableHTTPMCPTransport`，并保留以下可替换接口：

```python
class MCPTransport(Protocol):
    def list_tools(self, config: MCPServerConfig) -> list[MCPToolDefinition]: ...

    def call_tool(
        self,
        config: MCPServerConfig,
        tool: str,
        arguments: dict,
    ) -> object: ...
```

Transport 在调用时读取 `config.api_key_env` 对应的环境变量。它不返回、记录或持久化密钥值。
每个供应商都有独立并发上限，且同步 LangGraph 被异步 Web 服务调用时不会嵌套事件循环。

## 5. 使用示例

```python
transport = StreamableHTTPMCPTransport(env_file=".env")
seller_client = MCPProviderClient(
    config=seller_sprite_mcp_config(),
    transport=transport,
)
seller_sprite = SellerSpriteMCP(seller_client)

catalog = seller_client.discover_tools()
result = seller_sprite.keyword_miner(
    marketplace="US",
    keyword="phone stand",
    return_fields=["keyword", "searches", "purchases", "relevancy"],
)
```

Sif 的参数 Schema 可能演进，因此先执行 `discover_tools()`，使用其返回的 `input_schema` 校验参数，再调用端口：

```python
sif_client = MCPProviderClient(
    config=sif_mcp_config(),
    transport=transport,
)
sif = SifMCP(sif_client)

catalog = sif_client.discover_tools()
result = sif.asin_keyword_signals(validated_arguments)
```

注入 Listing 子图时可直接构建生产研究网关：

```python
researcher = build_keyword_research_gateway(env_file=".env")
services = ListingWorkflowServices(
    researcher=researcher,
    copywriter=copywriter,
    validator=validator,
)
```

## 6. 当前工具白名单

卖家精灵：

- `asin_detail`
- `traffic_source`
- `traffic_keyword`
- `keyword_order`
- `keyword_miner`

Sif：

- `market_get_asin_keyword_signals`
- `ops_get_listing_keyword_distribution`
- `market_get_asin_profile`
- `market_get_asin_aba_footprint`
- `market_screen_keyword_opportunities`
- `market_discover_competitors`
- `market_get_keyword_root_competitors`

白名单之外的工具在进入 Transport 前直接拒绝。

## 7. 查询与证据规则

- 首轮最多使用当前 ASIN、2 个竞品 ASIN 和 2 个种子词，防止一次任务无界扣费；
- 后续每轮最多增加 1 个竞品和 1 个扩展词，总计最多 3 轮；
- 卖家精灵和 Sif 可并行，同一供应商内按查询顺序执行；
- 查询指纹防止同一请求重复扣费；
- 原始响应只在确定性代码中解析，模型只获得统一的关键词证据。

## 8. 下一步

1. 启动时将必要工具 Schema 快照与当前工具目录比对；
2. 增加有 TTL 的查询缓存和针对临时错误的有界重试；
3. 使用一个具体商品输入执行首次真实关键词联调；
4. 实现 Listing 生成器和确定性质检器。

## 9. 实现位置

- `src/amazon_ops/listing/mcp.py`
- `src/amazon_ops/listing/mcp_transport.py`
- `src/amazon_ops/listing/keyword_gateway.py`
- `tests/test_listing_mcp.py`
- `tests/test_keyword_gateway.py`
