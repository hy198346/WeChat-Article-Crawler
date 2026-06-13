# WeChat-Article-Crawler 解读聚合 HTML 页面设计

## Goal

在不改变现有 `Server酱` 通知简洁格式的前提下，为微信公众号文章解读新增一个独立的 HTML 聚合页，用于查看全部历史解读结果。

页面目标：

- 汇总所有历史解读结果
- 按公众号分组展示
- 每个公众号默认展示最新一条解读
- 同一公众号的历史解读默认折叠
- 支持从页面直接跳转原文链接

同时，AI 解读应继续支持异步执行，不阻塞原有抓取与通知主流程。

## Scope

本次仅覆盖以下内容：

- 基于现有 `output/article_analysis/*.json` 生成一个静态总 HTML 页面
- 在单篇解读成功或批量抓取完成后刷新该页面
- 页面按公众号分组并折叠历史记录

本次不包含以下内容：

- 不修改现有 `Server酱` 通知正文格式
- 不引入新的 Web 服务、路由或数据库
- 不实现实时前端刷新
- 不新增搜索、筛选、分页、标签等复杂交互
- 不新增“每个公众号单独一个页面”的独立站点结构

## Current Context

当前项目已经具备以下能力：

- 文章抓取结果可落盘为 Markdown
- 单篇 AI 解读结果可落盘到 `output/article_analysis/*.json`
- 批量汇总结果可落盘到 `output/article_batches/*.json`
- `Server酱` 已有成熟通知链路，且目前用户希望继续保持原有通知样式

当前缺口是：

- AI 解读虽然已保存，但没有统一、可浏览的汇总页面
- 用户查看解读需要进入文件目录逐个打开 JSON 或 Markdown
- 批量通知不适合承载所有历史解读浏览需求

## Requirements

### Functional Requirements

1. 系统应生成一个总 HTML 页面，展示全部历史单篇解读结果。
2. 页面数据源应来自 `output/article_analysis/*.json`。
3. 页面应按 `account` 字段对解读结果分组。
4. 每个公众号分组中，应按发布时间倒序排列。
5. 每个公众号默认展示最新一条解读内容。
6. 同一公众号的更早历史解读应放入折叠区域。
7. 每条解读应展示：
   - 公众号名称
   - 文章标题
   - 发布时间
   - 原文链接
   - AI 主题
   - 核心观点
   - 适合谁看
   - 风险/注意点
8. 单篇解读完成后，应触发 HTML 重建。
9. 批量抓取结束后，也应触发 HTML 重建，确保总页面和当前落盘结果一致。
10. 页面生成失败不应影响抓取、解读和通知主流程。

### Non-Functional Requirements

1. 不引入额外运行依赖和新服务。
2. 输出文件应保持在现有 `output/` 目录体系内。
3. 页面结构应尽量简单，兼容本地直接打开。
4. 对损坏或字段不完整的 JSON 结果要有容错能力。

## Options Considered

### Option A: 静态总页面，运行后重建

做法：

- 每次解读完成后扫描 `output/article_analysis/*.json`
- 重建 `output/article_analysis/index.html`

优点：

- 实现最简单
- 与当前缓存和落盘模型天然兼容
- 不依赖本地服务
- 直接双击 HTML 即可查看

缺点：

- 页面不是实时响应式更新，而是生成式更新

### Option B: HTML + JSON 数据分离

做法：

- 生成 `index.html` 和 `index.json`
- 前端用浏览器端 JS 渲染数据

优点：

- 后续方便加筛选和搜索

缺点：

- 比当前需求更复杂
- 当前收益不明显

### Option C: 本地动态服务

做法：

- 启动本地 HTTP 服务，动态读取 `article_analysis` 目录

优点：

- 扩展性最好

缺点：

- 明显超出当前需求
- 维护成本高

## Recommended Approach

推荐采用 Option A：静态总页面，运行后重建。

原因：

- 最符合当前“通知不改、补一个可浏览解读页”的需求
- 不破坏现有脚本运行方式
- 不增加服务管理和运行复杂度
- 与现有缓存、文件输出、launchd 定时任务都容易集成

## Output Layout

新增输出文件：

- `output/article_analysis/index.html`

继续使用现有单篇解读输出：

- `output/article_analysis/<article_id>.json`
- `output/article_analysis/<article_id>.md`

说明：

- `index.html` 是一个只读聚合页
- 单篇 JSON 仍然是权威数据源
- HTML 页面不反向修改原始 JSON

## Data Model Assumptions

页面依赖的核心字段来自单篇解读 JSON：

- `article_id`
- `account`
- `title`
- `url`
- `published_at`
- `date`
- `topic`
- `core_points`
- `audience`
- `risks`
- `status`

仅纳入以下结果：

- `status = "ok"`

对于异常或不完整数据：

- 缺少 `account` 时归入 `Unknown_Account`
- 缺少 `published_at` 时回退到 `date`
- 缺少 `title` 时显示 `(无标题)`
- 缺少 `url` 时仅展示文本，不做链接
- 缺少解读字段时按空数组或空字符串降级

## Page Structure

页面采用纯静态 HTML + 内嵌 CSS，无需额外 JS 框架。

页面结构：

1. 页面标题区
   - 标题：例如“公众号 AI 解读汇总”
   - 副标题：显示生成时间、总公众号数、总解读数

2. 公众号分组区
   - 每个公众号一个分组卡片
   - 卡片头显示：
     - 公众号名
     - 该公众号解读总数
     - 最新解读时间

3. 默认展示区
   - 每组默认展示最新一条解读
   - 内容完整展开

4. 历史折叠区
   - 历史解读放入折叠块
   - 点击后展开所有历史记录

5. 单条解读卡片内容
   - 标题
   - 发布时间
   - 原文链接
   - 主题
   - 核心观点列表
   - 适合谁看
   - 风险/注意点列表

推荐使用原生 `<details>` / `<summary>` 实现折叠，减少 JS 依赖。

## Sorting And Grouping Rules

分组规则：

- 按 `account` 分组

组内排序规则：

- 优先按 `published_at` 倒序
- 若 `published_at` 缺失，则按 `date` 倒序
- 若时间字段均不可解析，则按文件修改时间或标题稳定排序兜底

页面分组排序规则：

- 按各公众号“最新一条解读时间”倒序排列

## Generation Triggers

页面重建应在以下时机触发：

1. 单篇解读成功落盘后
2. 批量抓取流程结束后

这样可以覆盖两类场景：

- 手工单篇调试时，页面及时更新
- 定时批量任务跑完后，页面完整更新

如果页面重建失败：

- 记录日志
- 不中断主流程
- 不影响单篇 JSON 缓存与通知发送

## Async Interpretation Behavior

“异步解读”在本次设计里的含义是：

- 抓取和通知主流程不依赖聚合 HTML 生成成功
- HTML 聚合页只消费已落盘的解读 JSON
- 单篇解读生成完成后，再异步或后置触发页面重建

本次不新增复杂任务队列，仅在现有执行链中增加一个“轻量重建页面”的后置步骤。

## Error Handling

需要处理以下异常情况：

1. 单篇 JSON 文件损坏
   - 跳过该文件
   - 写日志说明

2. JSON 字段缺失
   - 使用降级值渲染
   - 不因单条坏数据导致整个页面失败

3. 输出目录不存在
   - 自动创建 `output/article_analysis/`

4. HTML 写入失败
   - 记录错误
   - 不中断抓取和解读主流程

## Files To Change

### Code

- `scripts/wechat_article_crawler/article_analysis.py`
  - 增加聚合页面生成函数
  - 扫描单篇 JSON
  - 生成 `index.html`

- `scripts/wechat_article_crawler/wechat_crawler.py`
  - 在单篇解读成功后触发页面刷新
  - 在批量抓取结束后触发页面刷新

### Tests

- `tests/test_article_analysis.py`
  - 新增聚合页面生成测试
  - 验证分组、排序、最新一条默认展示、历史折叠结构
  - 验证坏 JSON 或字段缺失时不会整体失败

### Documentation

- `README.md`
  - 补充聚合 HTML 页面位置与用途说明

## Validation Plan

### Automated Validation

1. 运行单元测试，覆盖：
   - 多公众号分组
   - 同公众号按时间倒序
   - 最新一条在默认展示区
   - 历史解读在折叠区
   - 缺字段和坏 JSON 的容错

### Runtime Validation

1. 使用现有 `output/article_analysis/*.json` 生成一次 `index.html`
2. 检查页面中：
   - 存在多个公众号分组
   - 每组显示最新一条解读
   - 历史条目位于折叠区
   - 原文链接可点击
3. 在单篇解读完成后再次运行，确认页面内容自动更新

## Risks

1. 如果历史 JSON 中时间字段格式不一致，排序可能需要额外兜底逻辑。
2. 如果未来单篇 JSON schema 变化，HTML 生成逻辑需要同步更新。
3. 当历史数据量较大时，单页 HTML 体积会变大；本次先不做分页，后续如有需要再扩展。

## Acceptance Criteria

满足以下条件即视为完成：

1. 通知继续保持原有格式，不强制展示 AI 解读。
2. 系统生成 `output/article_analysis/index.html`。
3. 页面展示全部历史单篇解读结果。
4. 页面按公众号分组，默认展示每个公众号最新一条。
5. 历史解读可折叠展开。
6. 页面生成失败不会影响抓取、解读和通知主流程。
