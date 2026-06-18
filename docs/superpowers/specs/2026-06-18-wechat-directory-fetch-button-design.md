# WeChat-Article-Crawler 公众号目录页立即抓取按钮设计

## Goal

在现有 `output/article_analysis/index.html` 公众号解读目录页顶部，增加一个全局“立即抓取”按钮。

用户点击后，系统应立即执行一次“全部监控公众号”的手动抓取，并完成：

- 抓取最新文章
- 执行 AI 解读
- 刷新 `article_analysis` 目录页与账号页

本次按钮的目标是让用户在浏览目录页时，无需切回命令行，就能手动触发一轮完整刷新。

## Scope

本次覆盖：

- 在目录页顶部增加一个全局“立即抓取”按钮
- 新增一个本地 HTTP 接口，供目录页触发“抓取全部监控号”
- 复用现有全量抓取与解读链路
- 抓取完成后刷新目录页内容
- 为按钮补充前端状态反馈与并发保护
- 补充对应测试与 smoke 验证

本次不覆盖：

- 不在每个公众号目录项旁增加单独抓取按钮
- 不在单公众号详情页增加“立即抓取”按钮
- 不改现有“重新解读”单篇按钮协议
- 不引入前端框架、任务队列或数据库
- 不新增“选择部分公众号抓取”的 UI
- 不让该按钮额外发送 Server 酱通知

## Current Context

当前项目已经具备以下能力：

1. `scripts/wechat_article_crawler/article_analysis.py` 负责生成：
   - `output/article_analysis/index.html`
   - `output/article_analysis/accounts/<slug>.html`
2. 单篇文章已支持前台“重新解读”按钮。
3. `scripts/wechat_article_crawler/wechat_crawler.py` 已提供本地 HTTP 服务能力：
   - `POST /api/reanalyze`
   - 静态页联动服务
4. 全量抓取主链路已经存在：
   - `run_push_latest_all(...)`
5. `run_push_latest_all(...)` 已能完成：
   - 加载公众号清单
   - 拉取最新文章
   - 执行解读
   - 刷新分析页
   - 根据参数决定是否发送通知

当前缺口是：

- 目录页只有浏览能力，没有“全量立即抓取”入口
- 用户若想手动刷新全部监控号，只能回到命令行执行
- 现有“重新解读”是单篇粒度，不能覆盖“全量抓取最新文章”的场景

## User Intent

用户已经明确约束了本次按钮的行为：

- 按钮位置：目录页顶部
- 按钮范围：抓取全部监控号
- 执行动作：抓取 + 解读 + 刷新
- 通知语义：不额外发送通知
- 实现方式：保持传统静态页 + 本地接口模式

因此，本次设计的重点不是新增复杂任务系统，而是将现有批量抓取能力安全地暴露给目录页。

## Options Considered

### Option A: 目录页顶部按钮 + 新增本地批量抓取接口

做法：

- 在目录页顶部新增“立即抓取”按钮
- 页面调用新的本地 HTTP 接口，例如 `POST /api/fetch-latest-all`
- 接口内部复用 `run_push_latest_all(...)`
- 固定以 `push=False` 运行，避免通知副作用

优点：

- 最符合用户诉求
- 与现有“重新解读”前台交互模式一致
- 复用现有全量抓取主链路，改动边界清晰
- 能明确返回成功、失败、忙碌等状态

缺点：

- 需要新增一条接口与少量前端脚本
- 需要处理并发点击和执行中互斥

### Option B: 页面按钮直接启动命令行任务

做法：

- 页面按钮触发本地子进程，直接执行现有 CLI 参数，例如 `--push-latest-all`

优点：

- 初看实现较快

缺点：

- 页面和 CLI 耦合更重
- 日志、退出码、通知、副作用更难约束
- 后续测试和错误处理更不稳定

### Option C: 复用现有单篇重解读接口，循环触发

做法：

- 页面枚举目录页所有已有条目
- 对每个公众号或文章循环调用当前 `POST /api/reanalyze`

优点：

- 表面上少一个新接口

缺点：

- 接口语义错误
- 无法真正表达“抓取最新文章”
- 会把“全量抓取”错误拆成多次单篇重解读
- 执行效率和失败恢复都更差

## Recommended Approach

采用 Option A。

原因：

- 这是唯一同时满足“目录页顶部一个按钮”“抓取全部监控号”“抓取后完成解读并刷新”“不发通知”四个条件的自然方案。
- 它最大程度复用现有 `run_push_latest_all(...)` 能力，不需要为页面再造第二套抓取逻辑。
- 它和当前“静态 HTML + 少量内联 JS + 本地 API”风格一致，后续维护成本最低。

## Functional Requirements

1. `output/article_analysis/index.html` 顶部应出现“立即抓取”按钮。
2. 该按钮点击后，应触发一次“全部监控公众号”的手动抓取。
3. 手动抓取必须复用现有全量抓取与解读主流程，而不是重新实现一套逻辑。
4. 本次手动抓取不应发送 Server 酱通知。
5. 抓取成功后，应刷新目录页与单账号页，使页面显示最新结果。
6. 抓取执行中，按钮应禁用，避免重复点击。
7. 若已有一轮手动全量抓取在执行中，后续请求应收到 `busy` 类错误，而不是并行再跑一轮。
8. 前端必须展示至少三种状态：
   - 进行中
   - 成功
   - 失败
9. 失败时应保留明确错误原因用于日志，但前端可先统一提示简洁文案。

## Non-Functional Requirements

1. 保持静态 HTML 输出模式，不引入前端框架。
2. 保持本地 HTTP 服务方案，不引入数据库、消息队列或外部任务系统。
3. 与现有 `/api/reanalyze` 的来源校验风格保持一致，继续限制本地可信来源。
4. 目录页新增按钮后，不应影响现有单篇“重新解读”能力。
5. 页面样式与当前解读页保持一致，不做大规模 UI 重构。

## Design

### Entry Placement

按钮放在目录页顶部标题区，靠近“生成时间 / 账号数 / 解读数”这类摘要信息。

不放在每个公众号目录项旁边，原因是：

- 用户本次要的是一个全量入口，而不是逐号操作
- 避免目录项区域被按钮塞满
- 保持目录区的导航职责清晰

单公众号详情页本次不新增该入口，以避免一个动作出现在多个位置导致语义重复。

### API Shape

建议新增：

- `POST /api/fetch-latest-all`

请求体可以保持简单：

```json
{}
```

如后续需要扩展，也可兼容如下字段：

```json
{
  "trigger": "manual_directory_button"
}
```

返回结构建议统一为：

成功：

```json
{
  "status": "ok",
  "count": 12
}
```

执行中：

```json
{
  "status": "error",
  "reason": "busy"
}
```

失败：

```json
{
  "status": "error",
  "reason": "fetch_latest_all_failed:RuntimeError:..."
}
```

### Backend Execution Path

新增一个轻量包装函数，例如：

- `handle_fetch_latest_all_api_request(...)`

其职责是：

1. 校验请求来源
2. 检查当前是否已有全量手动抓取在执行
3. 调用 `run_push_latest_all(...)`
4. 固定传入：
   - `push=False`
   - `save_markdown` 沿用当前默认或现有配置
5. 返回统一的 JSON 结果

这里不建议页面直接调 CLI，而是直接在 Python 进程内复用已有函数，原因是：

- 错误可控
- 日志更统一
- 测试更容易
- 更容易做互斥

### Concurrency Guard

需要增加一个进程内互斥保护，避免用户连续点击触发两轮全量抓取。

建议方案：

- 在 `wechat_crawler.py` 内新增一个模块级锁或运行标记
- 进入 `fetch-latest-all` 时先尝试获取
- 若锁已被占用，直接返回：
  - `{"status":"error","reason":"busy"}`
- 执行完成或异常退出时，在 `finally` 中释放锁

本次只要求防止“同一服务进程内的重复触发”。
不要求跨多进程、跨机器分布式互斥。

### Frontend Behavior

目录页生成时新增：

- 一个按钮，例如 `class="fetch-latest-button"`
- 一个状态文字容器，例如 `class="fetch-latest-status"`
- 一段独立脚本，专门处理该按钮

交互流程：

1. 用户点击按钮
2. 按钮立即禁用
3. 状态显示“立即抓取中...”
4. 页面发起 `POST /api/fetch-latest-all`
5. 成功：
   - 状态改为“抓取成功，正在刷新...”
   - 短暂延迟后刷新页面
6. 失败：
   - 状态改为“立即抓取失败，请稍后重试”
   - 若为 `busy`，可提示“已有抓取任务进行中，请稍后再试”
   - 按钮恢复可点击

### Rendering Boundaries

建议把目录页的全量抓取按钮渲染与现有“重新解读脚本”分开，不混在同一个大函数里。

可以采用以下边界：

- 目录页标题区渲染
- 目录页“立即抓取”按钮渲染
- 目录页“立即抓取”脚本渲染
- 单篇“重新解读”脚本继续保留原职责

这样能避免两个前台动作互相污染：

- “立即抓取”是全量入口
- “重新解读”是单篇入口

### Data Flow

整体数据流如下：

1. 用户打开 `article_analysis/index.html`
2. 点击“立即抓取”
3. 浏览器请求 `POST /api/fetch-latest-all`
4. 后端进入全量抓取包装函数
5. 包装函数调用 `run_push_latest_all(..., push=False)`
6. 现有主链路完成：
   - 加载账号清单
   - 拉取最新文章
   - 生成或刷新解读
   - 刷新 `output/article_analysis/index.html`
   - 刷新 `output/article_analysis/accounts/*.html`
7. 接口返回成功
8. 页面自动刷新，用户看到最新目录结果

### Error Handling

需要覆盖以下情况：

1. 来源不可信
   - 返回 `forbidden_origin`
   - HTTP 状态码 403

2. 正在执行中
   - 返回 `busy`
   - HTTP 状态码 409 或 400 均可，但推荐 409

3. 微信认证失效
   - 返回现有明确错误，如 `wechat_auth_required`
   - 前端展示统一失败文案
   - 日志保留原始原因

4. 全量抓取过程中抛出异常
   - 返回 `fetch_latest_all_failed:<type>:<message>`
   - 前端提示失败
   - 锁必须被释放

5. 目录页重建失败
   - 视为抓取失败的一部分
   - 记录日志
   - 不返回伪成功

### Testing

需要补充以下测试：

1. 目录页 HTML 测试
   - 顶部出现“立即抓取”按钮
   - 顶部出现状态容器
   - 目录页脚本包含 `fetch-latest-all` 相关逻辑
   - 目录页仍不出现单篇 `reanalyze-button`

2. 接口测试
   - 请求成功时，会调用 `run_push_latest_all(..., push=False)`
   - 执行中时返回 `busy`
   - 来源不可信时返回 `forbidden_origin`
   - 异常时返回 `fetch_latest_all_failed:*`

3. 互斥测试
   - 连续两次触发时，第二次不会再启动一轮新抓取

4. smoke 验证
   - 重建目录页
   - 启动静态页服务
   - 启动 API 服务
   - 打开目录页
   - 点击“立即抓取”
   - 确认页面刷新且不发通知

## Risks

- 如果互斥只做前端禁用而后端不加锁，重复点击仍可能触发并发执行。
- 如果直接复用 CLI 而不是 Python 函数，会让返回状态和错误处理变得不稳定。
- 如果把全量抓取按钮脚本塞进单篇重解读脚本，后续维护会越来越混乱。

对应控制方式：

- 后端做真正互斥，前端禁用只作为补充
- 优先复用 `run_push_latest_all(...)` 函数级入口
- 将“全量抓取按钮”和“单篇重解读按钮”脚本明确分开

## Implementation Notes

预计主要改动文件：

- `scripts/wechat_article_crawler/article_analysis.py`
- `scripts/wechat_article_crawler/wechat_crawler.py`
- `tests/test_article_analysis.py`

如果接口测试更适合单独放到新测试文件，也可以新增一个聚焦 API 的测试文件，但应保持测试目的清晰，不做无关扩张。

## Validation Plan

推荐验证顺序：

1. 先补目录页与 API 相关失败测试
2. 实现顶部“立即抓取”按钮与接口
3. 运行聚焦测试
4. 重建 `output/article_analysis/index.html`
5. 本地执行一次手动点击 smoke
6. 若用于对外入口，重启静态页服务与 API 服务
