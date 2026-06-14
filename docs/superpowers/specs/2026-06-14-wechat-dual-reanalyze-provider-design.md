# WeChat-Article-Crawler 双按钮强制 provider 重解读设计

## Goal

在现有公众号 AI 解读静态页中，把单个“重新解读”入口升级为两个明确分工的按钮：

- `元宝解读`
- `本地模型解读`

两个按钮都必须是“强制指定引擎”语义，而不是默认链路或自动兜底。用户点击哪个按钮，就只走对应的分析引擎；失败时直接返回该引擎的失败结果，不静默切换到另一条链路。

## Scope

本次覆盖：

- 将解读卡片中的单个“重新解读”按钮改为两个 provider 明确的按钮
- 前端请求体增加 `provider` 字段，显式传递 `yuanbao` 或 `ollama`
- 后端重解读接口支持强制 provider 语义
- 让元宝和本地模型重解读都复用现有单篇抓取与落盘逻辑
- 为按钮渲染、前端脚本、接口参数校验和 provider 行为补充测试

本次不覆盖：

- 不新增第三种模型或“自动选择”按钮
- 不改造现有批量异步重试机制
- 不改 `summary`/`topic`/`core_points` 等分析数据结构
- 不重做页面布局，只在现有 action 区追加按钮与状态文案
- 不引入新的 Web 框架、任务队列或数据库

## Current Context

当前代码已经具备以下能力：

- `article_analysis.analyze_single_article()` 默认采用“元宝优先，失败再回退本地 Ollama”的单篇分析链路
- `_render_analysis_item_html()` 当前为每条文章渲染一个 `重新解读` 按钮
- `_render_reanalyze_script_html()` 当前向 `/api/reanalyze` 发送 `{article_id, url}` 请求
- `wechat_crawler.handle_reanalyze_api_request()` 与 `run_reanalyze_from_url()` 已经负责单篇抓取、强制重解读、落盘和刷新静态页
- 本地 Ollama 与 `news` 元宝接口都已经存在并被现有测试覆盖

当前缺口：

- 前端按钮语义不明确，用户无法直接选择“只走元宝”或“只走本地”
- 后端重解读接口没有 provider 参数，默认只能复用当前“元宝优先、本地兜底”的综合策略
- 对“点元宝就必须只走元宝”“点本地就必须只走本地”这一需求没有契约保证

## User Problem

用户已经明确提出两个要求：

1. 先抽检后台日志，确认现状没有新的异常堆积
2. 把“重新解读”改成两个按钮，分别对应元宝解读和本地模型解读（Ollama）

其中第二点的关键不是“多一个文案”，而是：

- 按钮语义必须清晰
- 引擎选择必须可控
- 不能点击一个按钮后仍然偷偷走另一条链路

## Requirements

### Functional Requirements

1. 每条可重解读文章必须渲染两个按钮：`元宝解读` 和 `本地模型解读`。
2. 两个按钮都必须携带相同的 `article_id` 和 `url`，并新增 `provider` 标识。
3. 点击 `元宝解读` 时，后端只能走 `news` 元宝接口；失败时直接报错，不得回退本地 Ollama。
4. 点击 `本地模型解读` 时，后端只能走本地 Ollama；不得调用元宝接口。
5. 两个按钮继续复用同一个重解读 API 路径，不新增第二套页面路由。
6. 无原文链接时，两个按钮都应禁用，并展示不可重解读提示。
7. 重解读成功后，仍需刷新对应文章缓存并重建静态页。
8. 接口必须校验 `provider`，仅接受 `yuanbao` 和 `ollama`。

### Non-Functional Requirements

1. 改动应尽量集中在现有渲染和重解读处理链路，不扩散到无关模块。
2. 前端继续保持原生 HTML/CSS/JS，不引入额外前端依赖。
3. 对外访问路径、`analysis_public_base_url` 和 `analysis_reanalyze_path` 的现有约定必须保持兼容。
4. 未点击按钮时，不改变现有批量分析和异步重试行为。

## Options Considered

### Option A: 单接口增加 `provider` 参数

做法：

- 页面仍调用现有 `/api/reanalyze`
- 请求体扩展为 `{article_id, url, provider}`
- 后端根据 `provider` 强制改写单篇分析配置

优点：

- 改动集中
- 不影响现有代理和公网路径配置
- 前后端契约清晰，测试入口复用度高

缺点：

- 需要在后端把“默认分析链路”和“强制 provider 模式”明确拆开

### Option B: 两个独立接口路径

做法：

- 新增 `/api/reanalyze/yuanbao`
- 新增 `/api/reanalyze/ollama`

优点：

- URL 语义最直接

缺点：

- 要同步修改路径注入、静态页脚本、反向代理和测试
- 复杂度高于需求本身

### Option C: 前端两个按钮，但继续复用默认“元宝优先、本地兜底”链路

做法：

- 只改按钮文案，不改后端 provider 行为

优点：

- 实现最快

缺点：

- 不满足用户“强制指定引擎”的明确要求
- 容易造成按钮语义与真实行为不一致

## Recommended Approach

推荐采用 Option A：单接口增加 `provider` 参数。

原因：

- 最符合“改动最小但语义明确”的目标
- 保留现有 `analysis_public_base_url` 和 `analysis_reanalyze_path` 机制
- 可直接在现有测试集上补 provider 断言，不需要再开第二套服务路径

## Design

### Frontend Rendering

`_render_analysis_item_html()` 中的 action 区从：

- 单个 `重新解读` 按钮

改为：

- `元宝解读` 按钮
- `本地模型解读` 按钮
- 一条共享的状态文本区域

每个按钮都应包含：

- `data-article-id`
- `data-url`
- `data-provider`

推荐按钮文案：

- `元宝解读`
- `本地模型解读`

无可用 URL 时：

- 两个按钮都加 `disabled`
- 状态文案显示 `缺少原文链接，无法重解读`

### Frontend Script

`_render_reanalyze_script_html()` 继续注入一段轻量原生 JS，但行为调整为：

1. 监听所有 `.reanalyze-button`
2. 点击后读取 `data-provider`
3. 根据 provider 生成对应状态文案
4. 向同一个 `REANALYZE_API_URL` 发送：

```json
{
  "article_id": "aid-123",
  "url": "https://mp.weixin.qq.com/s/xxx",
  "provider": "yuanbao"
}
```

或：

```json
{
  "article_id": "aid-123",
  "url": "https://mp.weixin.qq.com/s/xxx",
  "provider": "ollama"
}
```

建议状态文案：

- `元宝解读中...`
- `元宝解读成功，正在刷新...`
- `元宝解读失败，请稍后重试`
- `本地模型解读中...`
- `本地模型解读成功，正在刷新...`
- `本地模型解读失败，请稍后重试`

按钮忙碌态仍复用现有 `is-busy` 样式；同一条记录中的另一个按钮在请求进行中也应一并禁用，避免对同一文章重复点击产生并发重解读。

### Backend API Contract

`handle_reanalyze_api_request(payload, config, request_headers=None)` 增加 `provider` 解析与校验。

请求体新增字段：

- `provider`

允许值：

- `yuanbao`
- `ollama`

非法值或缺失时返回：

```json
{
  "status": "error",
  "article_id": "aid-123",
  "reason": "invalid_provider"
}
```

成功响应保持现有结构，额外保证 `source` 与实际 provider 一致：

```json
{
  "status": "ok",
  "article_id": "aid-123",
  "account": "测试号",
  "title": "文章标题",
  "source": "yuanbao"
}
```

### Provider Forcing Rules

核心设计是把“默认分析策略”和“强制 provider 模式”拆开。

建议在 `run_reanalyze_from_url()` 新增 `provider` 参数，并在进入 `_attach_single_article_analysis()` 前构造 provider-specific config：

- `provider == "yuanbao"`
  - 保留 `analysis_news_interpret_url`
  - 禁止本地 Ollama 作为兜底
  - 元宝失败时直接返回失败结果

- `provider == "ollama"`
  - 临时清空 `analysis_news_interpret_url`
  - 只走本地 Ollama
  - 不允许触发远端元宝请求

为了避免把“是否允许 fallback”硬编码在前端语义里，建议在分析层引入一个显式开关，例如：

- `analysis_force_provider`
- 或 `analysis_disable_fallback`

实现目标不是新增很多配置项，而是让 provider 行为在代码里可读、可测、可复用。

### Analysis Layer Changes

`analyze_single_article()` 当前逻辑是：

1. 先调用元宝
2. 元宝失败时回退本地

本次设计要求增加“强制模式”分支：

- 默认模式：保持现状，不影响其他调用方
- 强制元宝模式：只允许远端元宝，不回退本地
- 强制本地模式：直接跳过远端元宝，只跑本地

这样既满足新按钮需求，也不破坏既有单篇/批量分析调用点。

## Error Handling

### Invalid Provider

- 缺少 `provider`
- `provider` 不在允许列表内

返回 `invalid_provider`

### Yuanbao Forced Failures

`provider == "yuanbao"` 时，若远端超时或失败：

- 返回对应远端失败原因，例如 `news_interpret_timeout`
- 不触发本地 Ollama

### Ollama Forced Failures

`provider == "ollama"` 时，若本地模型失败：

- 返回 `ollama_timeout` 或 `ollama_error:*`
- 不触发元宝

### UI Feedback

前端继续使用泛化错误文案，不把底层异常直接暴露到按钮旁边：

- `元宝解读失败，请稍后重试`
- `本地模型解读失败，请稍后重试`

这样可以保持页面稳定，不把内部原因直接暴露给终端用户；详细原因仍由接口响应和日志保留。

## Testing

需要补充或调整以下测试：

### `tests/test_article_analysis.py`

1. `_render_analysis_item_html()`：
   - 渲染两个按钮
   - 两个按钮都带 `data-provider`
   - 无 URL 时两个按钮都禁用

2. `_render_reanalyze_script_html()`：
   - 注入 provider 字段
   - 状态文案区分元宝与本地模型
   - 点击某一按钮时会一并禁用同条目下的另一个按钮

3. `analyze_single_article()`：
   - 强制元宝模式成功时只调用远端
   - 强制元宝模式失败时不回退本地
   - 强制本地模式时不调用远端

### `tests/test_article_analysis.py` 中的 `wechat_crawler` 相关测试

1. `handle_reanalyze_api_request()`：
   - 透传 `provider`
   - provider 缺失或非法时返回 `invalid_provider`

2. `run_reanalyze_from_url()`：
   - 根据 provider 正确改写 config
   - 仍保持 `force_reanalyze=True`

## Verification Plan

实现阶段完成后，验证至少包括：

1. focused `pytest`：
   - 与 reanalyze 按钮渲染相关的测试
   - 与 `handle_reanalyze_api_request()` / `run_reanalyze_from_url()` 相关的测试
   - 与 `analyze_single_article()` provider 分支相关的测试

2. 静态页 smoke：
   - 重建分析页
   - 确认页面上出现两个按钮
   - 点击不同按钮时请求体 provider 正确

3. 运行态 smoke：
   - 元宝按钮在元宝链路可用时成功刷新
   - 本地按钮在本地 Ollama 可用时成功刷新

## Risks And Mitigations

### Risk 1: 误伤现有默认分析链路

风险：

- 如果直接改写 `analyze_single_article()` 默认分支，可能影响定时任务和其他调用点

缓解：

- 保持默认行为不变，仅在显式 provider 模式下切换策略

### Risk 2: 双按钮引发重复点击并发

风险：

- 同一文章上两个按钮被连续点击，导致重复重解读

缓解：

- 点击其中一个按钮后，同条目下两个按钮同时进入禁用态，直到请求完成

### Risk 3: provider 语义只做了前端，没有做后端强约束

风险：

- 页面看起来分成两个按钮，但服务端仍偷偷回退

缓解：

- 以后端强制 provider 规则为准，并用测试锁住“不回退”语义

## Out Of Scope Notes

以下能力后续如有需要再单独立项：

- 按钮旁展示底层错误原因
- 页面内展示最近一次 provider 选择记录
- 对同一文章的重解读做后端排队/去重
- 在首页目录页直接展示批量 provider 操作入口
