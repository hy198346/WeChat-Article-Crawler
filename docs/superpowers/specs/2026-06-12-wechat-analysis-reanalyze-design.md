# WeChat-Article-Crawler 解读页重新解读与公众号名修正设计

## Goal

在现有 `output/article_analysis/index.html` 解读聚合页基础上，补齐两项能力：

- 每条解读增加“重新解读”按钮，便于对失败、缺失或质量不理想的条目手动重跑
- 修正目录和分组里出现的错误公众号名，例如 `gh_2ba2404c01c0` 与 `Unknown_Account`

目标是让用户在本地页面中直接完成补救操作，而不是切回命令行手工执行单篇重跑。

## Scope

本次覆盖：

- 为解读聚合页的每一条记录渲染“重新解读”按钮
- 新增一个轻量本地 HTTP 接口，供页面触发单篇重解读
- 重解读成功后重建 `output/article_analysis/index.html`
- 优化公众号名规范化规则，避免将 `gh_*` 之类内部标识直接展示给用户
- 为上述行为补充单元测试与集成测试

本次不覆盖：

- 不新增数据库或外部任务队列
- 不改造现有 Server 酱通知格式
- 不支持跨机器远程调用该重解读接口
- 不新增复杂前端状态管理或单页应用框架
- 不做批量“全部重解读”入口

## Current Context

当前项目已具备以下基础能力：

- 单篇 AI 解读缓存落盘到 `output/article_analysis/<article_id>.json`
- 聚合页由 `build_analysis_index_html()` 基于现有 JSON 缓存生成
- 单篇直链抓取可通过 `run_extract_from_url()` 走完整抓取与解读流程
- 页面已支持目录、锚点跳转与历史折叠

当前缺口：

- 页面只有浏览能力，无法对异常条目直接补救
- 历史缓存中的错误账号名会污染目录与分组标题
- 仅靠重新跑定时任务修复失败条目，反馈慢且不直观

## User Problem

用户已经明确提出两个实际问题：

1. 某些文章没有解读成功，需要在页面上直接补跑
2. 部分缓存条目的 `account` 字段不正确，目录中出现 `gh_2ba2404c01c0 (1)`、`Unknown_Account (1)` 之类不友好的名称

因此，本次设计需要同时解决“重新执行”与“名称修正”两个问题，且要尽量复用现有抓取和解读逻辑。

## Requirements

### Functional Requirements

1. 聚合页中的每一条解读都应展示“重新解读”按钮。
2. 点击按钮后，系统应按该条解读对应的文章 `url` 重新抓取文章并重新执行 AI 解读。
3. 重解读应复用现有单篇抓取与解读逻辑，不新增第二套分析实现。
4. 重解读成功后，应更新对应的单篇 JSON/Markdown 缓存，并重建聚合页。
5. 前端应向用户反馈当前状态，至少包括“处理中”“成功”“失败”。
6. 若该条记录缺少可用 `url`，按钮应禁用或明确提示不可重解读。
7. 系统应规范化公众号名，避免将 `gh_*`、空字符串等内部或无效值直接用于目录与分组标题。
8. 当重解读重新抓到正确公众号名时，应使用新的展示名覆盖旧缓存中的错误名称。

### Non-Functional Requirements

1. 不引入新的长期运行服务依赖，接口应保持轻量、仅本地使用。
2. 页面继续保持简单 HTML 为主，只增加必要的少量原生 JS。
3. 即使重解读接口失败，也不能影响现有静态页面浏览。
4. 缓存目录与输出路径继续保持在 `output/` 体系内。

## Options Considered

### Option A: 只渲染按钮，不直接执行

做法：

- 页面中为每条记录生成一条命令提示
- 用户点击后复制命令，再去终端执行

优点：

- 实现成本最低
- 不需要新增接口

缺点：

- 用户仍要切换到终端
- 无法满足“直接在页面里补救”的诉求

### Option B: 页面调用轻量本地接口直接重跑

做法：

- 页面按钮调用本地 `POST /api/reanalyze`
- 后端复用 `run_extract_from_url()` 和现有分析逻辑
- 成功后重建聚合页并返回结果

优点：

- 体验最好
- 改动集中，逻辑复用高
- 不需要引入完整 Web 框架

缺点：

- 需要补一层本地 HTTP 服务
- 页面需要少量 JS 处理请求与状态提示

### Option C: 仅删除缓存，等待下次定时任务自动重跑

做法：

- 页面按钮只删除对应 JSON/Markdown 缓存
- 不立刻重解读，由下次任务兜底

优点：

- 后端实现简单

缺点：

- 反馈慢
- 无法保证用户点完马上修好

## Recommended Approach

推荐采用 Option B：页面调用轻量本地接口直接重跑。

原因：

- 最符合用户“防止有些没有解读成功”的即时补救需求
- 可以复用现有单篇直链抓取与 AI 解读逻辑
- 只需要一个本地用途的轻量接口，不必引入完整动态站点

## Architecture

方案分成三个层次：

1. 聚合页渲染层
   - 在每条解读卡片里增加按钮与状态区域
   - 若条目缺失有效链接，则按钮禁用

2. 本地重解读接口层
   - 新增极简 HTTP 服务，提供 `POST /api/reanalyze`
   - 请求参数至少包含 `article_id` 与 `url`
   - 服务端执行单篇重抓取、重解读、落盘与重建 index

3. 名称规范化层
   - 在构建聚合页时统一规范化 `account`
   - 在重解读成功写回缓存时优先使用重新抓到的展示名

## API Design

新增本地接口：

- `POST /api/reanalyze`

请求体：

```json
{
  "article_id": "abc123",
  "url": "https://mp.weixin.qq.com/s/xxxx"
}
```

成功响应：

```json
{
  "status": "ok",
  "article_id": "abc123",
  "account": "正确公众号名",
  "title": "文章标题"
}
```

失败响应：

```json
{
  "status": "error",
  "article_id": "abc123",
  "reason": "missing_url"
}
```

接口约束：

- 仅监听 `127.0.0.1`
- 不做登录态设计
- 不承诺高并发，只服务手工点击场景

## Frontend Behavior

每条解读卡片新增：

- “重新解读”按钮
- 状态文案区域

交互流程：

1. 用户点击按钮
2. 按钮进入禁用态，文案切换为“重新解读中...”
3. 页面调用 `POST /api/reanalyze`
4. 成功后显示“重新解读成功，正在刷新...”
5. 页面刷新，重新加载最新的 `index.html`
6. 失败时显示失败原因，按钮恢复可点击

无有效 `url` 时：

- 按钮置灰
- 状态提示“缺少原文链接，无法重解读”

## Data And Naming Rules

为避免目录继续出现错误账号名，新增统一规范化规则：

### Invalid Account Detection

以下情况视为无效账号名：

- 空字符串
- 仅空白字符
- `Unknown_Account`
- 以 `gh_` 开头的内部标识

### Preferred Account Source

账号名优先级：

1. 重抓文章页时提取出的展示名，例如 `js_name`、`nickname`
2. 调用入口显式传入的公众号名
3. 旧缓存中的 `account`，但前提是其值不是无效账号名
4. 回退到 `Unknown_Account`

### Rendering Behavior

构建聚合页时：

- 对 `account` 先做规范化
- 规范化后仍无有效账号名时，统一归入 `Unknown_Account`
- 目录和分组锚点均基于规范化后的账号名生成

### Repair Behavior

重解读成功后：

- 若重新抓到了有效展示名，应写回该条缓存 JSON 的 `account`
- 重新生成 `index.html` 后，旧的 `gh_*` 分组应自然消失

## Code Changes

### `scripts/wechat_article_crawler/article_analysis.py`

新增或调整：

- 增加账号名规范化辅助函数
- 在渲染条目 HTML 时输出按钮、状态区域与所需的 `data-*` 属性
- 在构建聚合页时注入少量原生 JS，用于调用重解读接口
- 对目录分组使用规范化后的账号名

### `scripts/wechat_article_crawler/wechat_crawler.py`

新增或调整：

- 暴露一个可复用的单篇“强制重解读”入口
- 允许忽略 `analysis_skip_if_exists`，对指定文章强制重跑
- 新增轻量本地 HTTP 服务入口，处理 `POST /api/reanalyze`
- 重跑成功后调用 `build_analysis_index_html()` 刷新聚合页

## Error Handling

需要处理以下错误：

1. `url` 缺失
   - 接口返回 `missing_url`
   - 前端提示不可重解读

2. 微信文章抓取失败
   - 接口返回抓取错误原因
   - 不修改原有缓存

3. AI 解读失败
   - 接口返回 `ollama_error:*` 或 `ollama_timeout`
   - 保留旧缓存，避免用失败结果覆盖成功结果

4. HTML 重建失败
   - 接口仍返回错误
   - 日志保留详细时间戳

## Testing Plan

### Unit Tests

补充到 `tests/test_article_analysis.py`：

- 账号名规范化测试
- `gh_*` 和空值回退到 `Unknown_Account` 的测试
- 条目渲染包含“重新解读”按钮的测试
- 缺少 URL 时按钮禁用的测试

### Integration Tests

补充到现有集成测试：

- 调用重解读入口时会复用现有单篇抓取逻辑
- 强制重解读会绕过 `analysis_skip_if_exists`
- 重解读成功后会触发聚合页重建
- 重解读成功后错误公众号名被修正

### Runtime Validation

1. 启动本地服务与静态页
2. 打开 `output/article_analysis/index.html`
3. 对某条记录点击“重新解读”
4. 确认按钮进入处理中状态
5. 确认请求成功后页面刷新
6. 确认原先的 `gh_...` 或 `Unknown_Account` 分组被修正或减少

## Risks

1. 某些历史缓存条目若 `url` 已失效，仍无法自动修正名称。
2. 若本地未启动配套接口，页面按钮将不可用或报连接错误。
3. 如果旧缓存缺少足够字段，部分条目只能继续留在 `Unknown_Account` 分组。

## Acceptance Criteria

满足以下条件即视为完成：

1. 聚合页每条解读都能看到“重新解读”按钮。
2. 有有效链接的条目点击后可以直接重跑，并刷新页面结果。
3. 缺失链接的条目不会误触发重跑，而是给出明确提示。
4. `gh_*` 之类内部账号标识不再直接展示为目录或分组标题。
5. 重新抓到正确公众号名后，聚合页按修正后的名称分组显示。
6. 重解读失败不会破坏原有静态页面浏览能力。
