# WeChat-Article-Crawler 异步解读自动重试设计

## Goal

提高公众号文章异步解读成功率，避免当前“异步任务失败一次就结束”的行为。

本次设计要实现两类处理：

- 可恢复失败自动重试，直到成功或达到人为停止条件
- 外部条件导致的失败发送通知提示，但不做无限重试

这里的“外部条件失败”主要指系统本身无法在当前时刻自行修复的问题，例如：

- 微信鉴权失效
- 触发微信安全验证
- 原文 URL 已删除、失效或不可访问
- 配置缺失导致任务根本无法执行

目标不是承诺所有文章立刻成功，而是让系统对“可恢复失败”具备持续补跑能力，并对“不可恢复失败”尽早告警，减少静默失败。

## Scope

本次覆盖：

- 改造异步单篇解读任务，使其具备自动重试能力
- 为异步 job 增加失败分类、重试次数与下一次调度时间等元数据
- 在异步 job 执行失败后，根据失败类型决定“继续重试”或“发送通知后停止”
- 为重试逻辑补充测试
- 为失败分类与通知行为补充最小可观测日志

本次不覆盖：

- 不改前台手动“重新解读”按钮的同步请求语义
- 不改 batch summary 的自动重试策略
- 不引入数据库、Redis、Celery 或外部任务队列
- 不承诺对永久不可恢复错误做无限重试
- 不改 server 酱消息模板以外的第三方通知通道

## Current Context

当前异步解读链路大致如下：

1. 抓取到新文章后，使用 `_schedule_async_job()` 调度 `_attach_single_article_analysis()`
2. 在 `process` 模式下，任务会被序列化到 `output/async_jobs/*.json`
3. 子进程以 `--run-async-job-file` 方式执行任务
4. `_attach_single_article_analysis()` 最终调用 `analyze_single_article()`
5. 单篇解读失败时，会直接落盘失败结果并结束任务

当前缺口：

1. 异步任务失败后没有统一自动重试逻辑
2. `news_interpret_timeout`、`ollama_timeout` 这类瞬时失败会直接终止
3. `wechat_auth_required` 之类外部条件失败与瞬时失败没有统一调度策略
4. job 文件只承担“投递一次”的作用，不承担重试状态记录

## User Problem

用户希望异步任务不要因为单次失败就放弃，而是尽可能把所有文章解读补成功。

同时，用户也明确接受这样一个边界：

- 如果失败是外部条件造成的，系统不需要无限尝试
- 但必须发通知提醒，避免无声失败

因此，本次不是简单“加几次 retry”，而是要把异步任务从“一次性执行”升级为“可恢复失败自动追成功，不可恢复失败及时通知”的模型。

## Requirements

### Functional Requirements

1. 异步单篇解读任务在失败后必须根据失败类型自动决定后续动作。
2. 可恢复失败必须自动重试。
3. 外部条件失败必须发送通知提示，并停止自动重试。
4. 任务只有在得到有效成功解读后，才算最终完成。
5. 同一文章在任一时刻只能存在一个活跃异步解读 job，避免失败后重复堆积。
6. 重试过程必须保留上下文信息，至少包括：
   - 当前尝试次数
   - 重试模式
   - 最近失败原因
   - 下一次重试时间
   - 首次失败时间
   - 最近失败时间
7. 成功后应删除对应 job 文件或将其标记为完成，不再继续参与调度。
8. 停止重试的外部条件失败，必须能在日志或通知中明确看到原因。

### Non-Functional Requirements

1. 继续使用当前文件型异步任务模型，不引入外部队列依赖。
2. 失败分类逻辑应集中管理，避免散落在多个入口硬编码。
3. 重试策略必须可配置，至少支持默认值和显式覆盖。
4. 实现不能破坏现有同步抓取、手动重解读和静态页生成流程。

## Failure Taxonomy

本次需要把失败分成两大类：

### Recoverable Failures

这些失败默认认为“稍后再试可能成功”，需要自动重试：

- `news_interpret_timeout`
- `ollama_timeout`
- `empty_summary`
- `empty_analysis`
- `reanalyze_failed:*`
- 短暂网络异常、临时 HTTP 失败、上游瞬时错误

### External-Condition Failures

这些失败默认认为“当前系统自身无法修复”，需要通知后停止自动重试：

- `wechat_auth_required`
- `wechat_security_verification_required`
- 明确的 `invalid_url`
- 明确的文章不可访问、已删除、需要重新登录等错误
- 缺失关键配置导致无法执行的错误

如果某个错误暂时无法精确分类，默认先归到可恢复失败，避免误停；但涉及鉴权、安全验证、非法输入、明确 4xx 语义时应优先归入外部条件失败。

## Options Considered

### Option A: 在 `_attach_single_article_analysis()` 内部做同步循环重试

做法：

- 一个异步 worker 进程里直接循环调用 `analyze_single_article()`
- 失败时 sleep 后继续下一次尝试

优点：

- 实现最直接
- 改动面小

缺点：

- worker 生命周期变长，单个进程会长时间阻塞
- 重试状态对外不可见
- 不利于未来扩展为更复杂的调度

### Option B: 基于 job 文件的持久重试调度

做法：

- job 文件除了原始 payload，还携带 attempt、next_retry_at、last_reason 等状态
- 每次失败后，更新 job 文件并重新调度下一次执行
- 成功或外部条件失败后，结束 job 生命周期

优点：

- 与现有 `output/async_jobs/*.json` 模式一致
- 失败状态可追踪
- 可以清晰支持“继续重试”与“停止并通知”

缺点：

- 状态管理比一次性 job 稍复杂

### Option C: 新增独立扫描守护进程

做法：

- job 失败后只写状态
- 由额外 watchdog 周期性扫描并拉起重试

优点：

- 调度职责最清晰

缺点：

- 需要新增额外服务
- 超出当前仓库已有部署复杂度

## Recommended Approach

采用 Option B。

原因：

- 它最贴合当前项目已有的 `async_jobs` 文件投递模型
- 可以最小化部署变化
- 既能保留 detached process 的隔离优势，又能让重试状态持久化
- 后续如果要扩到 batch job，也能复用同一模式

## Design

### Job Model

当前 job 结构需要扩展为两部分：

- `payload`
- `retry_state`

建议新增字段：

- `attempt`: 当前尝试序号，从 1 开始
- `retry_mode`: 例如 `until_success` 或 `stop_on_external`
- `first_failed_at`
- `last_failed_at`
- `last_reason`
- `next_retry_at`
- `stop_reason`
- `notified`

其中：

- 可恢复失败持续调度直到成功
- 外部条件失败直接写入 `stop_reason` 并通知

### Retry Policy

默认策略建议如下：

- `attempt 1` 失败后，`10s` 后重试
- `attempt 2` 失败后，`60s` 后重试
- `attempt 3` 失败后，`300s` 后重试

达到长时间连续失败时：

- 不停止重试
- 但应进入更长的稳定退避周期，例如固定每 `30m` 或 `60m` 重试一次
- 并可按节流规则追加提醒，避免长期无声失败

### External-Condition Handling

对外部条件失败：

1. 不再继续自动重试
2. 发送通知
3. 在日志中打印结构化信息
4. 保留失败分析结果，便于静态页展示“失败可重试/需人工处理”

通知内容至少应包含：

- 公众号名
- 文章标题
- 文章 URL
- 失败原因
- 提示动作，例如“请刷新微信鉴权后手动或自动补跑”

### Success Criteria

任务成功的唯一条件是：

- `analysis.status == "ok"`
- 且内容非空

只要仍然是：

- `skipped`
- 空分析
- 超时
- 其它失败态

都不能视为成功。

### Failure Classification Helper

建议新增集中辅助函数，例如：

- `_classify_async_analysis_failure(reason) -> "recoverable" | "external" | "unknown"`

用途：

- 统一判断失败类型
- 避免在 `_run_async_job_file()`、`_attach_single_article_analysis()`、通知逻辑中重复写字符串判断

### Scheduling Boundary

推荐把“是否继续重试”的调度决策放在异步 job 执行边界，而不是埋进 `article_analysis.analyze_single_article()` 内部。

原因：

- `analyze_single_article()` 仍然保持“单次解读”的职责
- 重试策略属于任务编排层，不属于单次分析层
- 更便于未来复用到不同入口

## Data Flow

### Happy Path

1. 调度异步 job
2. job 子进程执行 `_attach_single_article_analysis()`
3. 得到有效 `ok` 分析
4. 落盘分析结果
5. 刷新静态页
6. 删除 job 文件

### Recoverable Failure Path

1. 执行单篇解读
2. 返回可恢复失败
3. 更新 `retry_state`
4. 计算 `next_retry_at`
5. 重新写回 job
6. 重新调度下一次执行
7. 持续重复直到得到有效成功结果

### External Failure Path

1. 执行单篇解读
2. 返回外部条件失败
3. 记录最终失败状态
4. 发通知
5. 不再重试

## Testing Strategy

需要补以下测试：

1. 可恢复失败第一次失败后会重新生成带下一次调度时间的 job
2. 可恢复失败在后续尝试成功后会删除 job
3. 外部条件失败会停止重试并触发通知
4. 可恢复失败不会因为固定次数上限而停止自动重试
5. 同一文章不会生成多个重复活跃 job
6. 现有同步流程和前台 `重新解读` 不受影响

## Risks

1. 如果失败分类过粗，可能把本该继续重试的错误误判为外部条件失败，导致提前停住。
2. 如果 job 去重做不好，失败任务可能在高频抓取下不断堆积。
3. 如果通知节流缺失，外部条件失败可能造成重复告警噪音。

## Open Questions Resolved

1. 是否要对所有失败无限重试？
   - 否。用户已确认：外部条件导致的失败只通知，不做无限尝试。
2. 是否引入外部任务队列？
   - 否。继续使用当前文件型 async job 模型。
3. 是否同时改 batch summary？
   - 否。第一版只覆盖异步单篇解读。
