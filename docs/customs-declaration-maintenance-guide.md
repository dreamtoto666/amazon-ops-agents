# 报关单填写模块维护与交接手册

> 适用对象：需要维护、调整或发布“报关单填写”功能的产品、运营和开发人员。本文覆盖美国报关单模块；欧洲报关表是相邻但独立的功能，位于 `european_customs_declaration/`，不要把两者的规则混用。

## 1. 模块用途与边界

“报关单填写”把两份 Excel 文件中的货件数据按渠道与贸易方式归类，校验数据后，基于内置商品目录和 Excel 模板生成一个 ZIP 包。

- 它是**确定性规则**功能：不使用 LLM、LangGraph、MCP 或数据库。
- 输入、预览结果和生成的文件均不持久化。
- 它**只生成文件**，不执行任何对外报关、调价或运营操作。
- 所有报关规则、商品价格和模板调整都需要经过业务/合规负责人确认后再上线。

完整业务规则见 [报关单填写自动化](customs-declaration-automation.md)。

## 2. 使用流程

用户在控制台打开 `/dashboard/customs-declarations` 后：

1. 上传一八发货模板（`.xlsx`）和 FBA 货件表（`.xlsx`）。
2. 点击“分析文件”，系统展示八个分类的货件、箱数、重量、金额和商品汇总，以及警告/错误。
3. 仅在 `can_generate=true` 时可点击“生成并下载 ZIP”。
4. 系统为有货的分类各生成一个报关单 Excel，并打包为 `{上海日期}-报关单.zip`。

前端入口、同源代理和后端的关系如下：

```text
浏览器页面
  /dashboard/customs-declarations
       │
       ├── POST /api/customs-declarations/preview
       └── POST /api/customs-declarations/generate
                │（Next.js 同源代理，携带当前登录用户身份）
                ▼
FastAPI /api/customs-declarations/*
                ▼
CustomsDeclarationService
  解析 Excel → 校验 → 分类/汇总 → OOXML 填表 → ZIP
```

接口均要求已登录用户。直接访问后端接口时需提供系统认可的认证信息；日常测试优先通过前端页面进行。

## 3. 输入、分类和生成规则速查

### 3.1 输入文件

| 文件 | 必需工作表 | 使用字段 |
| --- | --- | --- |
| 一八发货模板 | `美国专线箱单` | D 列“客户原单号”；表头“走货渠道”“贸易方式” |
| FBA 货件表 | `装箱明细` | 货件单号、总箱数、总重量 |
| FBA 货件表 | `货件详情` 或 `货件详细` | 货件单号、品名、SKU、申报量 |

表头名称用于定位字段；货件单号、箱数和重量支持在同一货件分组中向下继承。商品明细以“货件详情/货件详细”为准，**不是**“装箱明细”中的品名、SKU 或申报量。

### 3.2 分类

系统固定生成下列八个预览分类：

- 普船统配、快船、美森极致达、普船特惠；
- 每种渠道再按 `0110`、`9810` 贸易方式拆分。

渠道映射在 `src/amazon_ops/customs_declaration/service.py` 的 `_SHIPPING_CHANNELS` 中维护。客户原单号相同但被分到不同分类时，系统会阻止整批生成。

### 3.3 计算与模板

- 商品按“品名 + 规格数字”匹配，SKU 仅用于提示品名与 SKU 的冲突。
- 报关单价读取商品目录的 `unit_price_35`；金额为数量 × 单价，保留两位小数。
- EPDM 的 HS 编码为 `4016939000`，其它材质为 `3926909090`。
- 每个货件的箱数和毛重只累计一次；净重固定为总毛重的 92%。
- 商品重量按“数量 × 目录单件重量”比例分摊；最后一项承接四舍五入尾差。
- `0110` 最多 23 个标准商品；`9810` 最多 16 个。超过容量时整批不生成。
- 生成器直接编辑模板内的 OOXML，不通过 Excel 库重新保存模板，以保留六联单、图片、格式和公式。

## 4. 代码与资源地图

| 目的 | 位置 | 修改时的责任 |
| --- | --- | --- |
| 核心处理逻辑 | `src/amazon_ops/customs_declaration/service.py` | 解析、校验、分类、汇总、OOXML 写入 |
| 数据模型/预览返回结构 | `src/amazon_ops/customs_declaration/models.py` | 修改接口返回字段时同步前端类型 |
| 商品目录 | `src/amazon_ops/customs_declaration/resources/product_catalog.v1.json` | 商品、SKU、规格、材质、单重、35% 单价 |
| 0110 模板 | `src/amazon_ops/customs_declaration/resources/customs_declaration_0110.v1.xlsx` | 合同号、固定内容、表格布局 |
| 9810 模板 | `src/amazon_ops/customs_declaration/resources/customs_declaration_9810.v1.xlsx` | 合同号、固定内容、表格布局 |
| FastAPI 接口 | `src/amazon_ops/api.py` | `/preview`、`/generate` 的上传、认证和响应 |
| 后端单元/生成测试 | `tests/test_customs_declarations.py` | 规则、计算、模板内容与容量测试 |
| API 测试 | `tests/test_customs_declaration_api.py` | 认证、预览、ZIP 与 422 返回测试 |
| 前端页面 | `frontend/src/app/dashboard/customs-declarations/page.tsx` | 页面标题与挂载 |
| 前端工作台 | `frontend/src/features/customs-declarations/components/customs-declaration-workbench.tsx` | 上传、预览、错误与下载交互 |
| 前端类型与请求 | `frontend/src/features/customs-declarations/api/` | 返回类型、上传字段、下载处理 |
| 同源代理 | `frontend/src/app/api/customs-declarations/` | 浏览器到 Python API 的转发 |
| 侧边栏导航 | `frontend/src/config/nav-config.ts` | 菜单名称、图标、入口 |

## 5. 首次配置与启动

### 5.1 必备软件

- Git
- Python 3.11 或更新版本
- `uv`（Python 依赖管理工具）
- Node.js 与 npm（建议使用项目当前稳定版）
- Docker Desktop（推荐，用于启动 PostgreSQL 和完整联调）

### 5.2 获取项目和本地配置

在 PowerShell 中执行：

```powershell
git clone https://github.com/dreamtoto666/amazon-ops-agents.git
Set-Location amazon-ops-agents
Copy-Item .env.example .env
```

报关单模块本身不需要模型或 MCP 密钥。但完整系统的登录、数据库和其他功能依赖 `.env` 中的配置。`.env` 和 `frontend/.env.local` 仅限本机，绝不能提交到 GitHub、聊天记录或截图中。

首次安装依赖：

```powershell
uv sync --extra dev --no-editable
Set-Location frontend
npm install
Set-Location ..
```

### 5.3 推荐启动方式：Docker 完整联调

```powershell
docker compose up --build -d
docker compose ps
```

- 前端：`http://localhost:3001`
- 后端健康检查：`http://127.0.0.1:8000/api/health`
- PostgreSQL：本机默认端口 `5432`

停止服务但保留数据库数据：

```powershell
docker compose down
```

不要使用会删除数据卷的命令，例如 `docker compose down -v`，除非已明确获得授权。

查看服务日志：

```powershell
docker compose logs -f api
docker compose logs -f frontend
```

### 5.4 本地开发启动方式

先启动数据库：

```powershell
docker compose up -d postgres
```

在两个 PowerShell 窗口分别运行：

```powershell
# 窗口 1：后端（项目根目录）
$env:PYTHONPATH = "$PWD\src"
uv run uvicorn amazon_ops.api:app --host 127.0.0.1 --port 8000 --reload
```

```powershell
# 窗口 2：前端
Set-Location frontend
npm run dev
```

仓库的 `scripts/start-dev.sh` 可在 Git Bash、WSL 或 Linux/macOS 中一键启动 API 和前端；它不会替你启动 PostgreSQL。

## 6. 修改功能的标准做法

### 6.1 只新增或修订商品、SKU、规格、材质、重量、价格

这是最常见也最应优先采用的改法。

1. 先确认业务提供的品名、规格、SKU、材质、单件重量和 35% 报关单价。
2. 复制 `product_catalog.v1.json` 为新版本，例如 `product_catalog.v2.json`，保留历史版本用于回溯。
3. 在新目录中添加或修订商品。每项至少包含：

   ```json
   {
     "id": "product-xxx",
     "skus": ["F00000"],
     "standard_name": "标准品名和规格",
     "unit_weight": "0.10",
     "unit_price_35": "3.50",
     "declaration_name": "报关品名",
     "model": "规格型号",
     "material": "材质",
     "usage": "用途"
   }
   ```

4. 修改服务初始化时使用的新目录文件名，并更新加载器支持的 `version`（当前 `_load_catalog` 只接受版本 `1`）。
5. 在 `tests/test_customs_declarations.py` 增加“输入品名 → 命中规则 → 单价/HS 编码/金额”的测试。
6. 使用真实格式的脱敏样例进行预览与生成验证。

不要为了单个商品在 `service.py` 中硬编码例外。确有供应商别名时，优先评估能否规范上游品名；经确认后才可在 `_PRODUCT_NUMBER_ALIASES` 增加最小范围的映射，并必须补测试和说明。

### 6.2 增加或修改走货渠道

1. 确认新渠道应归属普船统配、快船、美森极致达或普船特惠中的哪一类。
2. 修改 `service.py` 中 `_SHIPPING_CHANNELS` 对应列表。
3. 在 `test_shipping_channel_groups_are_classified_exactly` 中增加精确渠道名测试。
4. 用包含该渠道的一八发货文件执行预览，确认落在正确的“渠道 × 贸易方式”分类中。

不要用宽松的字符串包含匹配替换现有映射；错误分类的报关风险高。

### 6.3 修改输入 Excel 的工作表或表头

1. 先保留一份脱敏的旧格式和新格式样例。
2. 修改 `_parse_shipment_assignments` 或 `_parse_fba` 中的工作表名、必需表头或字段继承逻辑。
3. 修改页面中的上传说明，避免用户继续按旧格式上传。
4. 修改 `docs/customs-declaration-automation.md` 的输入说明。
5. 增加旧格式兼容或明确移除兼容的测试；不得只凭人工试一次。

注意：发货模板的客户原单号当前固定读取 D 列；渠道和贸易方式按表头定位。FBA 商品明细以货件详情表的表头定位，不应误改为读取装箱明细商品字段。

### 6.4 修改重量、金额、HS 或校验规则

这些变更会直接影响申报结果，应先得到书面业务确认，并在变更说明中记录规则来源、生效日期与负责人。

1. 在 `service.py` 修改相应计算/校验函数。
2. 在单元测试中加入覆盖边界值、分母/数量异常、四舍五入尾差和阻断错误的用例。
3. 更新 `docs/customs-declaration-automation.md`。
4. 用固定样例断言预览数字和最终 Excel 的关键单元格，不能只验证“成功生成 ZIP”。

### 6.5 修改报关模板或要填写的单元格

1. 先复制现有模板，保留可回退版本；不要直接覆盖唯一模板文件。
2. 检查模板仍包含名为 `报关单` 的工作表，以及代码要写入的所有单元格。
3. 贸易方式模板容量与每项占用 3 行的布局必须一致。若容量改变，同时修改 `TEMPLATE_CAPACITIES`。
4. 如合同号改变，确认模板 `报关单!A10` 的值正确；文件名从这里读取。
5. 修改 `_render_workbook` 中的写入单元格映射时，保留 OOXML 直接写入方式，避免 `openpyxl` 重存导致图片、线条或打印区域损坏。
6. 运行生成测试，逐项检查工作表、关键单元格、公式、样式和媒体文件是否保留。

## 7. 接口或页面改动的同步清单

若新增或修改预览字段、上传字段、状态码或下载行为，必须同步修改以下位置：

1. `models.py`：Pydantic 请求/响应模型；
2. `api.py`：FastAPI 路由、认证和错误返回；
3. `tests/test_customs_declaration_api.py`：接口测试；
4. `frontend/src/features/customs-declarations/api/types.ts`：TypeScript 类型；
5. `frontend/src/features/customs-declarations/api/service.ts`：前端请求与下载逻辑；
6. `frontend/src/features/customs-declarations/components/`：页面展示与用户提示；
7. `frontend/src/app/api/customs-declarations/`：同源代理；
8. `docs/customs-declaration-automation.md`：用户可见规则和限制。

浏览器端不得绕过 `frontend/src/app/api/` 直接访问 Python API，否则会引入认证、跨域或线上路径问题。

## 8. 测试与验收

### 8.1 每次后端规则改动至少执行

```powershell
uv run pytest tests/test_customs_declarations.py tests/test_customs_declaration_api.py
```

若改动影响公共 API、认证或应用装配，再执行完整后端测试：

```powershell
uv run pytest
```

### 8.2 每次前端 TypeScript 改动至少执行

```powershell
Set-Location frontend
npm run typecheck
```

### 8.3 人工验收建议

使用已脱敏、格式真实的两份样例文件，确认：

- 正确显示八个分类；
- 有货分类的箱数、毛重、净重、数量和金额正确；
- 未知品名、缺少货件、非法重量/数量和跨分类单号会阻止生成；
- SKU 与品名不一致时出现警告，统计仍按品名；
- 下载 ZIP 后，文件数量与有货分类一致；
- 每个 Excel 有六个工作表，固定格式/图片/公式仍存在；
- `0110` 和 `9810` 分别选用了正确模板。

## 9. GitHub 协作流程

### 9.1 开始前

```powershell
git switch main
git pull --ff-only origin main
git switch -c feat/customs-product-catalog-v2
git status
```

分支命名建议：

- `feat/customs-...`：新增能力
- `fix/customs-...`：缺陷修复
- `docs/customs-...`：仅文档
- `chore/customs-...`：维护性调整

开始前先执行 `git status`。工作区中已有但与本次无关的修改不应顺带提交、删除或格式化。

### 9.2 开发、提交和推送

```powershell
# 完成修改并运行测试后
git diff --check
git diff -- src/amazon_ops/customs_declaration tests/test_customs_declarations.py docs
git add src/amazon_ops/customs_declaration tests/test_customs_declarations.py docs/customs-declaration-automation.md
git commit -m "feat(customs): add confirmed product catalog entries"
git push -u origin feat/customs-product-catalog-v2
```

然后在 GitHub 创建 Pull Request（PR），描述应包括：

- 为什么修改，以及业务/合规确认来源；
- 涉及的渠道、商品、模板或字段；
- 是否影响历史商品、价格、HS、重量或模板；
- 已执行的测试命令和结果；
- 脱敏截图或关键预览/Excel 校验结果；
- 回退方式（通常是回退该 PR 或恢复旧目录/模板版本）。

至少应由熟悉报关业务的人复核规则，由开发人员复核代码与测试。不要将真实客户文件、密钥、用户名密码或未脱敏合同信息上传到 PR。

### 9.3 合并后

1. 确认 CI 通过并完成所需审查。
2. 合并 PR 后，按部署环境发布；本地 Docker 可重新构建：`docker compose up --build -d`。
3. 使用脱敏样例复测预览和 ZIP。
4. 若发现报关结果异常，立即停止使用新版本并回退对应 PR/资源版本，再排查原因。

## 10. 常见问题排查

| 现象 | 常见原因 | 首先检查 |
| --- | --- | --- |
| 页面提示未登录或请求 401 | 会话失效、代理未携带身份 | 重新登录；检查同源代理，不要直接从浏览器调用 Python API |
| 找不到工作表/表头 | 上游 Excel 改版 | 工作表名和第一行表头；再决定兼容还是阻断 |
| 未知品名 | 目录没有该品名+规格映射 | 商品目录、规范化后的品名、数字规格；不要临时改金额绕过 |
| SKU 冲突警告 | SKU 与品名匹配到不同规则 | 上游数据和目录 SKU；当前统计以品名规则为准 |
| 无法生成 ZIP | 存在任一阻断错误，或所有分类为空 | 预览中的 `errors`；生成不会返回部分文件 |
| 模板容量不足 | 标准商品数超过 0110/9810 上限 | 商品合并规则、模板容量和是否需业务提供新模板 |
| 生成后的格式/图片丢失 | 模板被重存或 OOXML 映射不匹配 | 模板版本、`_render_workbook`、生成测试 |
| 前端 404 或接口不通 | 缺少/错误同源 Route Handler | `frontend/src/app/api/customs-declarations/` 与后端路径 |

## 11. 发布前最终检查

- [ ] 业务规则、价格、HS 和模板变更已获得确认。
- [ ] 新/改商品使用目录版本化，而非代码临时特例。
- [ ] 后端与前端类型、服务、代理、测试、文档已同步。
- [ ] 已运行受影响的 pytest 和前端 typecheck。
- [ ] 已用脱敏真实格式样例验证预览与最终 Excel。
- [ ] `.env`、密钥、客户原始文件和敏感数据未进入 Git。
- [ ] PR 描述写明影响、测试、回退方式和审核人。
