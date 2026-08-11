# GPU Price Monitor

一个面向个人研究和价格观察的 GPU 价格监控工具。项目使用 Flask 提供 Web 控制台，使用 Playwright 驱动独立 Chromium 浏览器采集闲鱼公开搜索结果，并把当前价格、每日历史、统计图表和推荐结果统一展示出来。

> 仅用于个人学习、研究和价格观察。请遵守闲鱼及其他服务的用户协议、robots 规则和适用法律；不要绕过登录、验证码、访问控制或平台风控，也不要高频访问平台。

## 当前定位

- 采集方式：仅保留“模拟浏览器”，不把传统 HTTP/API 作为主采集入口。
- 采集平台：当前界面和主流程固定使用闲鱼（Goofish）。
- 采集范围：内置 RTX 30/40/50 系列型号，也支持添加自定义型号。
- 采集周期：一次点击“开始采集”只完成一轮；本轮每个型号只采集一次，完成后浏览器自动关闭。
- 浏览器：使用项目专用 Chromium profile，不复用日常浏览器。首次使用闲鱼时，需要在控制台打开登录页并扫码验证。
- 数据：当前价格按商品 URL 去重；历史价格按商品和自然日去重，每天保留当天最后一次采样。

## 功能

- 闲鱼搜索结果的浏览器采集、滚动和分页
- 采集结果逐页批量写入 SQLite
- GPU 型号精确匹配，包括 `RTX 5090`、`RTX 5090 D`、`RTX 5090 D V2`
- 过滤低价引流、非显卡商品、维修/坏卡/拆机/矿卡和明显欺诈标题
- 自定义型号添加、删除
- 内置型号隐藏、恢复
- 当前价格、每日历史、最低价/平均价/中位数统计
- 单日数据箱型图 + 散点图，多日数据趋势图
- 低价推荐和可信度惩罚
- 采集进度、运行状态、任务日志
- CSV 导出
- SQLite WAL、批量事务、快照缓存和前端增量刷新

## 快速开始

### 1. 创建虚拟环境

Windows PowerShell：

```powershell
cd F:\source\Python\gpu-price-monitor
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
playwright install chromium
```

如果系统中的 `python` 不是刚创建的虚拟环境解释器，请使用虚拟环境的绝对路径运行后续命令。

### 2. 启动服务

```powershell
python app.py
```

默认访问地址：<http://127.0.0.1:5000>

如需更换端口：

```powershell
$env:GPU_MONITOR_PORT = "5001"
python app.py
```

请使用 `python app.py` 启动，不建议使用 `flask run`。采集器使用 Playwright 的同步 API；把它放进异步事件循环可能出现：

```text
It looks like you are using Playwright Sync API inside the asyncio loop
```

### 3. 首次登录闲鱼

1. 打开 Web 控制台。
2. 点击“闲鱼登录”，在项目专用浏览器中完成扫码。
3. 点击“校验登录”，确认状态显示为已登录。
4. 选择要采集的型号并开始采集。

浏览器会话保存在 `.sessions/`，专用 Chromium 数据保存在 `.chrome_profile/`。这两个目录包含登录态，不能提交到 Git，也不要分享给他人。

## 使用流程

```text
选择型号
  ↓
配置关注型号和过滤阈值
  ↓
打开并验证闲鱼登录
  ↓
开始采集
  ↓
浏览器逐个处理型号
  ↓
过滤和批量保存商品
  ↓
查看价格、趋势、推荐和日志
  ↓
本轮结束，浏览器自动退出
```

如果要再次采集，需要再次点击开始采集。程序不会在后台自动无限循环采集。

## 数据规则

### 当前价格

`prices` 表保存每个商品的最新状态。相同商品 URL 再次出现时，会更新标题、价格和更新时间，不会产生重复当前商品。

### 每日历史

`price_history` 表保存商品的每日快照：

- 同一商品当天采集多次：更新当天快照，保留最后一次采样。
- 第二天再次采集：新增第二天快照，前一天历史保留。
- 新 URL 商品：当天新增一条历史记录。

因此，当前页面展示最新市场状态，趋势图使用按日保留的历史数据。

### 价格过滤

过滤器会综合判断标题、型号、价格和商品属性，例如：

- 价格明显低于型号基准的引流商品
- “仅包装/支架/散热器/配件”等非显卡商品
- “维修、坏卡、拆机、矿卡、工程样品”等商品
- 型号不匹配或把基础型号与 Ti/Super/V2 混写的商品

过滤并不保证平台商品真实可靠，最终仍应打开原始链接核验卖家、成色、保修和交易条件。

## 项目架构

```text
浏览器前端
    │
    ▼
Flask API（app.py）
    ├── Crawler / BrowserManager（crawler.py）
    │      └── GoofishCrawler：闲鱼浏览器采集
    ├── ListingFilter（listing_pipeline.py）
    ├── Database（database.py）
    ├── MarketSnapshot（market_data.py）
    ├── Charts（charts.py）
    ├── Recommend（recommend.py）
    ├── GPU model registry（gpus.py）
    └── Settings cache（settings_store.py）
```

核心数据流：

```text
Playwright 页面
  → 商品卡片解析
  → 型号和价格规范化
  → ListingFilter 过滤
  → database.add_prices 批量写入
  → catalog_revision 递增
  → market_data 快照失效
  → 前端按 revision 增量刷新
```

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `app.py` | Flask 入口、页面渲染和全部 Web API 路由 |
| `crawler.py` | Playwright 浏览器生命周期、采集队列、闲鱼采集和单轮调度 |
| `database.py` | SQLite 初始化、状态、当前价格、历史价格和批量事务 |
| `gpus.py` | GPU 型号目录、精确匹配、5090 变体、自定义/隐藏型号 |
| `listing_pipeline.py` | 商品过滤策略和过滤统计，独立于 Flask/浏览器 |
| `market_data.py` | 当前市场数据快照和 revision 缓存 |
| `settings_store.py` | 设置默认值、校验、缓存和持久化 |
| `charts.py` | 趋势图、概览图、单日箱型/散点图和图表缓存 |
| `recommend.py` | 基于市场基准和可信度的低价推荐 |
| `http_crawler.py` | 备用 HTTP 解析器；当前不是主采集入口 |
| `api_panel.py` | 备用平台 API 配置和调用兼容层 |
| `templates/index.html` | Web 控制台 HTML 模板 |
| `static/app.js` | 页面初始化、API 调用、状态轮询和交互逻辑 |
| `static/style.css` | 深色玻璃卡片、响应式布局和统一视觉 token |
| `static/charts/` | 运行时生成的图表 PNG |
| `tests/` | Flask、采集器、数据库、过滤器、图表、推荐和前端静态检查 |
| `docs/plans/` | 审查记录、设计方案和性能架构说明 |
| `requirements.txt` | Python 运行依赖 |

## API 概览

| 接口 | 说明 |
| --- | --- |
| `GET /` | 控制台页面 |
| `POST /api/control/start` | 启动专用浏览器 |
| `POST /api/control/start_crawl` | 开始一轮采集 |
| `POST /api/control/pause` | 暂停采集 |
| `POST /api/control/resume` | 恢复采集 |
| `POST /api/control/stop` | 停止采集并关闭浏览器 |
| `GET /api/status` | 浏览器、任务、进度和数据状态 |
| `GET /api/prices` | 当前价格；支持 `since=<revision>` 增量请求 |
| `GET /api/history` | 商品历史价格 |
| `GET /api/trend` | 型号趋势数据 |
| `GET /api/series_chart` | 趋势图 |
| `GET /api/stats` | 市场统计和概览图 |
| `GET /api/recommend` | 低价推荐 |
| `GET /api/log` | 采集日志 |
| `GET/POST/PATCH/DELETE /api/models` | 型号查询、添加、隐藏、恢复和删除 |
| `GET/POST /api/settings` | 过滤和采集设置 |
| `GET /api/export` | CSV 导出 |
| `POST /api/clear` | 清理价格数据 |

## 性能设计

- SQLite 使用 WAL，降低读写互相阻塞。
- 商品页面按批次写入，减少事务提交次数。
- 型号、设置和市场统计使用内存缓存。
- 前端通过 `catalog_revision` 判断数据是否变化。
- 数据未变化时，`/api/prices?since=...` 只返回很小的 unchanged 响应。
- 图表按数据库路径、平台、型号和 revision 缓存。
- 采集任务使用阻塞队列，避免空轮询消耗 CPU。

## 测试

```powershell
python -m unittest discover -s tests -v
```

也可以先做静态检查：

```powershell
python -m py_compile app.py crawler.py database.py gpus.py http_crawler.py charts.py recommend.py listing_pipeline.py market_data.py settings_store.py
node --check static/app.js
git diff --check
```

## 运行文件和备份

以下文件/目录属于本地运行环境，通常不应提交：

```text
prices.db
.sessions/
.chrome_profile/
__pycache__/
server*.log
flask*.log
```

可以在升级前复制 `prices.db` 作为备份。项目中已有的 `prices.before_filter_20260810.db` 是历史过滤规则升级前的备份。

## 已知边界

- 闲鱼是否返回结果受登录状态、页面风控、网络质量和平台页面结构影响。
- 浏览器采集比纯 HTTP 请求消耗更多 CPU 和内存，但更接近用户实际访问路径。
- SQLite 适合单机、低并发部署；如果未来需要多进程、多用户并行写入，应迁移到 PostgreSQL 等服务型数据库。
- 图表 PNG 和浏览器缓存是运行时产物，删除后会在需要时重新生成。

## 许可证与责任

仓库当前未声明开源许可证。除非项目所有者另行授权，请不要将代码或采集结果用于商业分发。使用本项目产生的访问、交易和合规责任由使用者自行承担。
