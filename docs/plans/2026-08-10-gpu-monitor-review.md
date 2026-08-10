# GPU Monitor Review and UI Unification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修复项目中可复现的后端、采集、数据与前端问题，并交付统一、响应式且安全的 GPU 价格监控工作台。

**Architecture:** 保留 Flask、SQLite、Playwright 与原生前端的现有分层。通过小范围数据层 API、明确的采集状态机、前端安全渲染工具和 CSS 设计令牌修复问题，避免引入新框架或迁移用户数据。

**Tech Stack:** Python 3、Flask、SQLite、Playwright、原生 HTML/CSS/JavaScript、`unittest`、本地浏览器验证。

---

### Task 1: 建立回归测试基线

**Files:**
- Create: `tests/test_database.py`
- Create: `tests/test_gpus.py`
- Create: `tests/test_crawler.py`
- Create: `tests/test_app.py`

**Step 1: 写失败测试**

覆盖 Ti/Super 精确匹配、桌面显卡误过滤、自定义型号系列、空 URL 商品去重、清理当前与历史数据、采集异常后可重启、停止等待可中断、API secret 脱敏、HTTP/API 模式无需浏览器和趋势文件名安全。

**Step 2: 运行测试确认失败**

Run: `python -m unittest discover -s tests -v`

Expected: 新增断言在当前实现上出现失败，但测试只使用临时 SQLite 文件和 mock，不访问外网。

**Step 3: 记录当前语法基线**

Run: `python -m compileall -q app.py api_panel.py charts.py crawler.py database.py gpus.py http_crawler.py recommend.py`

Expected: PASS。

### Task 2: 修复型号、数据层与图表边界

**Files:**
- Modify: `gpus.py`
- Modify: `database.py`
- Modify: `charts.py`
- Test: `tests/test_gpus.py`
- Test: `tests/test_database.py`

**Step 1: 实现最具体型号匹配**

将标题归一化，并按具体关键词匹配；冲突检测忽略目标型号的基础子串。移除会误杀普通桌面卡的宽泛 Mobile 标记，未知自定义型号要求标题实际命中自定义名称。

**Step 2: 修复数据库语义**

为连接设置合理超时；让无 URL 商品按自身 ID 保留而不是全部归为一条；新增带锁的 `clear_prices()` 同时清理 `prices` 与 `price_history`；新增轻量计数方法供状态展示。

**Step 3: 安全生成图表文件名**

趋势图文件名只使用受限字符或稳定摘要，并容忍单点数据、未知平台和无图例场景。

**Step 4: 运行定向测试**

Run: `python -m unittest tests.test_gpus tests.test_database -v`

Expected: PASS。

### Task 3: 修复采集生命周期、配置和元数据

**Files:**
- Modify: `crawler.py`
- Modify: `app.py`
- Modify: `api_panel.py`
- Test: `tests/test_crawler.py`
- Test: `tests/test_app.py`

**Step 1: 收紧配置输入**

验证采集模式、浏览器模式、数值阈值和布尔值；拒绝 NaN、无限值、负数以及不合理的比例组合，并规范平台/型号列表。

**Step 2: 修正采集状态机**

用锁保护 start/stop，暂停与恢复只对合法状态生效；在 `_loop` 的 `finally` 中清理运行标记；用可中断 Event 替换轮次与任务 sleep，避免停止后旧线程继续执行。

**Step 3: 修正采集结果处理**

自定义型号使用传入的系列/代次；`keep_min` 在全部候选低于阈值时仍保留合法最低价；平台页面始终在 `finally` 关闭；关键词使用 URL 编码。

**Step 4: 修正 API 语义与 secret 处理**

应用导入时初始化数据库；清理接口调用数据层并清历史；模式切换不把停止状态伪装成暂停；API 配置响应只返回“已配置”状态，secret 留空时保留旧值。

**Step 5: 运行定向测试**

Run: `python -m unittest tests.test_crawler tests.test_app -v`

Expected: PASS。

### Task 4: 修复前端功能与安全渲染

**Files:**
- Modify: `static/app.js`
- Modify: `templates/index.html`

**Step 1: 强化请求层**

检查 HTTP 状态、JSON 类型和网络失败；关键按钮使用统一忙碌状态并显示可理解错误。

**Step 2: 消除竞态和重复请求**

用 `init()` 顺序加载设置、型号与范围；每轮慢刷新只请求一次 stats，并等待推荐/图表更新；防止重叠轮询。

**Step 3: 修复控制逻辑**

HTTP/API 模式直接启动采集，仅 browser/auto 要求浏览器；状态卡使用真实数据长度；浏览器模式切换和登录校验正确处理失败。

**Step 4: 安全输出动态内容**

添加文本、属性和 URL 安全工具，覆盖型号、标题、平台、日志、推荐、统计和链接；自定义型号不再能破坏 DOM；外链添加安全协议限制。

**Step 5: 检查 JavaScript 语法**

Run: `node --check static/app.js`

Expected: PASS。

### Task 5: 统一 GPU 遥测工作台界面

**Files:**
- Modify: `templates/index.html`
- Modify: `static/style.css`

**Step 1: 重组信息架构**

添加语义化 header/main/nav，将核心状态、采集与关注放入首屏网格；为设置、连接、洞察和数据区建立锚点与一致跨度，不删除任何已有功能或 ID。

**Step 2: 建立设计令牌与组件规则**

统一色彩、排版、间距、圆角、边框、阴影、按钮、表单、徽章、表格、图表、日志、模态框与空状态；移除内联样式。

**Step 3: 补齐可访问性与响应式**

添加 label/aria-live/progress/dialog 语义、键盘焦点、可操作星标按钮、密码输入、桌面/平板/手机断点和 reduced-motion。

**Step 4: 浏览器桌面验证**

Open: `http://127.0.0.1:5000/`

Expected: 1280px 首屏显示品牌、四项指标、关注行情与采集控制；无横向溢出、无控制台错误。

**Step 5: 浏览器移动验证**

Viewport: 390×844。

Expected: 单列布局、表单与按钮可触达、表格可横向滚动、页面本身无横向溢出。

### Task 6: 完整验证与复审

**Files:**
- Modify: `README.md`（仅在运行说明与行为确有不一致时）

**Step 1: 运行完整测试**

Run: `python -m unittest discover -s tests -v`

Expected: PASS。

**Step 2: 运行编译与前端语法检查**

Run: `python -m compileall -q .`

Run: `node --check static/app.js`

Expected: 均 PASS。

**Step 3: Flask 冒烟测试**

用 test client 检查 `/`、`/api/status`、`/api/prices`、`/api/stats`、`/api/models`、`/api/settings`、`/api/api_config`，预期均返回 200 且 JSON 不含明文 secret。

**Step 4: 浏览器交互复查**

检查筛选、分页、趋势空状态、模式切换前端逻辑、动态文本转义和 console；不点击真实采集、登录、清空或外部购买链接。

**Step 5: 最终代码复审**

逐项对照审计清单，确认没有修改 `.chrome_profile/`、`.sessions/` 和现有 `prices.db`，并记录仍受第三方平台/API 权限约束的非代码限制。
