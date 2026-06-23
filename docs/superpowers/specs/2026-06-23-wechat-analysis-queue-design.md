# WeChat-Article-Crawler 30 分钟解读队列与失败重试设计

## Goal

在保持微信公众号文章主抓取触发时间不变的前提下，将“抓取最新文章”和“文章解读执行”解耦：

- 主抓取任务继续只负责抓取、落盘和通知
- 自动解读改为统一进入队列，由独立 `launchd` worker 每 30 分钟消费
- 队列不仅包含新抓到的文章，也包含之前解读失败、允许继续重试的文章

目标是降低主触发窗口内的并发压力，同时保留当前默认在线解读链路能力。

## Scope

本次覆盖：

- 将主抓取后的自动解读调度从“立即拉起子进程”调整为“写入统一队列”
- 为自动解读新增每 30 分钟触发一次的独立 `launchd` worker
- 让新文章和历史失败文章共用同一套队列状态模型
- 保留现有单篇解读与聚合页落盘逻辑，重点改造调度层
- 为队列状态流转、失败重试和 worker 互斥补充测试与验证方案

本次不覆盖：

- 不修改当前主抓取 `launchd` 触发时刻
- 不新增数据库、外部消息队列或常驻任务系统
- 不改造在线解读 provider 内部实现
- 不把元宝、豆包、千问拆成三套独立队列
- 不改变前台手动“重新解读”能力的对外语义

## Current Context

当前项目已有以下基础能力：

- 主抓取定时任务为 `config/launchd/com.wechat.articlecrawler.runproject.plist`
- 主抓取入口在 `scripts/wechat_article_crawler/wechat_crawler.py`
- 自动解读调度已具备 `output/async_jobs/` 落盘能力
- 当前 `_schedule_async_job(...)` 在 `process` 模式下会立即 `_spawn_async_job_process(...)`
- 单篇解读结果会落盘到 `output/article_analysis/<article_id>.json`
- 聚合页刷新逻辑已存在，可复用

当前问题在于：

1. 主抓取发现新文章后，会在同一触发窗口内立即启动异步解读子进程
2. 批量解读流程仍在一次任务中串行遍历本轮变更文章，压力依然集中在主触发时刻
3. 之前解读失败的文章虽有部分重试机制，但没有统一纳入一个稳定的“半小时调度队列”
4. 自动解读和失败补跑的调度语义分散，不利于观察和运维

## User Problem

用户明确提出的要求有四个：

1. 当前主抓取触发时间保持不变
2. 必须先抓取最新文章，再进入队列型解读
3. 自动解读应复用当前默认在线解读链路，不额外把 provider 拆分成多队列
4. 队列必须包含之前解读失败的文章，而不只是新文章

补充背景：

- 当前在线解读链路包含元宝、豆包、千问等模块
- 用户判断整体在线解读压力理论上不大，因此重点不是扩容 provider，而是把主抓取窗口和解读窗口解耦

## Requirements

### Functional Requirements

1. 主抓取任务在抓到新文章后，应只将文章加入自动解读队列，不再立即执行自动解读子进程。
2. 自动解读队列必须支持新文章入队，也必须支持历史失败文章继续参与后续消费。
3. 队列中的同一篇文章必须基于 `article_id` 去重，避免重复排队。
4. 新增独立 `launchd` worker，每 30 分钟触发一次，消费待解读队列。
5. 队列 worker 消费时，应继续复用当前默认在线解读链路，而不是把元宝、豆包、千问拆成独立任务。
6. 单篇文章解读成功后，结果应继续落盘到现有 `output/article_analysis/` 目录，并刷新聚合页。
7. 可恢复失败的文章应保留在统一队列体系内，并在后续 worker 运行时继续被处理。
8. 外部条件导致的失败任务应被明确标记，避免在每轮 worker 中无限重试。
9. 若主抓取再次遇到一篇已有失败记录的文章，系统应能刷新该任务状态并重新进入可处理队列。

### Non-Functional Requirements

1. 主抓取窗口中的解读并发压力应显著下降，主任务重点只保留抓取、保存和通知。
2. 新增运行期产物必须继续落在 `output/` 和 `logs/` 目录体系下。
3. 队列 worker 必须具备互斥保护，避免多次并发消费同一批任务。
4. 调度层改造应尽量复用现有异步 job 文件结构和单篇解读逻辑，降低回归风险。
5. 日志中应能明确区分入队、消费、成功、重试、外部失败和跳过等状态。

## Options Considered

### Option A: 统一队列 + 30 分钟 worker

做法：

- 主抓取阶段只入队，不立即执行解读
- 新增独立 `launchd` worker，每 30 分钟消费一次队列
- 队列统一覆盖新文章和历史失败文章

优点：

- 最符合“先抓取，再队列型解读”的目标
- 可以真正削弱主触发窗口内的解读压力
- 新文章与失败补跑的调度模型统一，运维更清晰

缺点：

- 自动解读结果的最终生成时间会延后到下一次 worker
- 需要补充一层队列状态管理与 worker 互斥逻辑

### Option B: 保留即时异步 + 增加 30 分钟补偿 worker

做法：

- 保留当前主抓取后立即起异步解读子进程
- 另加一个每 30 分钟的 worker，只补跑失败或遗漏任务

优点：

- 对现有行为影响较小
- 队列补偿逻辑较容易接入

缺点：

- 主触发窗口的解读压力仍然存在
- 与用户“先抓取，再队列型解读”的目标不完全一致

### Option C: 仅做主任务内限流

做法：

- 不新增独立 worker
- 继续在主抓取后自动解读，但限制同时执行的任务数量

优点：

- 代码改动可能较少

缺点：

- 解读仍然发生在主任务窗口，只是变慢
- 无法形成统一的失败补跑队列
- 不能满足用户对“队列包含历史失败文章”的明确要求

## Recommended Approach

推荐采用 Option A：统一队列 + 30 分钟 worker。

原因：

- 完整满足“先抓取最新文章，再队列型解读”的核心要求
- 能把主抓取与解读执行彻底解耦，削峰效果最好
- 可以把历史失败文章纳入同一套状态流转，避免调度分裂
- 不需要把在线 provider 复杂化，仍然复用当前默认在线解读链路

## Architecture

整体方案分三层：

1. 抓取与入队层
   - 主抓取任务继续按现有固定时刻运行
   - 发现新文章后只负责保存文章信息、发送通知、写入自动解读 job

2. 队列消费层
   - 独立 `launchd` worker 每 30 分钟运行一次
   - worker 负责扫描、锁定、执行和更新 job 状态

3. 结果落盘层
   - 单篇解读继续复用现有默认在线解读链路
   - 成功结果继续写入现有 `output/article_analysis/`
   - 成功后刷新聚合页，不改变前台读取方式

## Queue Model

### Queue Storage

推荐继续沿用现有 `output/async_jobs/` 作为自动解读队列目录，不新增第二套运行目录。

理由：

- 已有 job 文件落盘基础
- 现有异步调度逻辑与测试可部分复用
- 目录口径与仓库规则一致

### Job Identity

每个自动解读 job 以 `article_id` 作为主身份。

去重规则：

1. 若同一 `article_id` 已存在活跃 job，则新抓取不重复创建第二个 job
2. 若同一 `article_id` 已存在失败 job，则允许刷新元数据并重置为可处理状态
3. 若同一 `article_id` 已成功完成，默认不因重复抓取而再次自动入队，除非后续有显式强制重解读需求

### Job States

建议统一使用以下状态：

- `pending`
  - 新入队，等待 worker 消费
- `running`
  - 当前正被某次 worker 处理
- `retry_waiting`
  - 上次执行失败，但属于可恢复失败，等待下一轮或指定时间后重试
- `done`
  - 已成功完成
- `failed_external`
  - 因外部条件失败，不应在每轮无限重试

### Job Payload

单个 job 至少应包含：

- `job_type`
- `article_id`
- `fetched`
- `config_snapshot`
- `status`
- `attempt`
- `last_reason`
- `first_failed_at`
- `last_failed_at`
- `next_retry_at`
- `updated_at`

其中 `fetched` 应尽量保留足够的文章元信息，避免 worker 再次处理时缺少标题、账号名或 URL。

## Data Flow

### 主抓取流程

1. 主抓取任务按当前固定时刻运行
2. 抓取最新文章并生成文章 payload
3. 立即发送 `Server酱` 或其他通知，不等待解读完成
4. 对每篇需要自动解读的文章调用“入队函数”
5. 入队函数基于 `article_id` 去重，并写入或更新 job 文件
6. 主抓取任务结束，不再立即启动自动解读子进程

### 30 分钟 worker 流程

1. `launchd` 触发 `analysis-queue` worker
2. worker 先申请全局互斥锁
3. 扫描 `output/async_jobs/` 中可处理的 job
4. 优先处理 `pending`
5. 再处理 `next_retry_at` 已到期的 `retry_waiting`
6. 对每个 job：
   - 先标记为 `running`
   - 调用现有默认在线解读链路
   - 成功则标记为 `done`
   - 可恢复失败则标记为 `retry_waiting`
   - 外部条件失败则标记为 `failed_external`
7. 每处理完一个成功任务，按现有逻辑落盘并刷新聚合页
8. worker 结束前释放互斥锁

## Failure Strategy

### Recoverable Failures

以下失败应视为可恢复失败：

- 在线解读超时
- 临时网络错误
- 解读结果为空
- provider 返回非预期但不属于确定性外部失败

处理方式：

- job 进入 `retry_waiting`
- 增加 `attempt`
- 记录 `last_reason`、`last_failed_at`
- 设置 `next_retry_at`

### External Failures

以下失败应视为外部条件失败：

- 微信鉴权失效
- 登录态失效
- 文章已删除或不可访问
- URL 非法
- 其他明确不可由简单重试恢复的错误

处理方式：

- job 进入 `failed_external`
- 保留失败记录和日志
- 不在每轮 worker 中无限自动重试

### Failed Article Re-entry

为满足“队列要包含之前解读失败的文章”的要求，系统应支持失败任务重新进入可处理状态：

1. 若失败类型为 `retry_waiting`，worker 在到期后自动继续处理
2. 若主抓取再次遇到同一文章且当前 job 为失败状态，应刷新 job 元数据并重新置为 `pending` 或 `retry_waiting`
3. 若后续提供人工修复或补跑入口，也应复用同一 job 状态体系，而不是新增另一套补跑通道

## File Layout

新增或修改的目标文件建议包括：

- `config/launchd/com.wechat.articlecrawler.analysis-queue.plist`
- `bin/run_analysis_queue_launchd.sh`
- `scripts/wechat_article_crawler/wechat_crawler.py`
- `tests/test_analysis_queue_schedule.py`

运行期产物：

- `output/async_jobs/*.json`
- `logs/launchd.analysis-queue.out.log`
- `logs/launchd.analysis-queue.err.log`

## Service Design

### New Launchd Service

新增服务标签：

- `com.wechat.articlecrawler.analysis-queue`

职责：

- 每 30 分钟执行一次自动解读队列消费

建议配置：

- `WorkingDirectory = <repo>`
- `RunAtLoad = false`
- 使用 `StartCalendarInterval` 配置每小时 `0` 分和 `30` 分触发
- `StandardOutPath` 写入 `logs/launchd.analysis-queue.out.log`
- `StandardErrorPath` 写入 `logs/launchd.analysis-queue.err.log`

### Startup Script

新增启动脚本应：

1. 自动推导仓库根目录
2. 自动创建 `logs/`
3. 自动读取根目录 `.env`
4. 调用统一入口，例如：

```bash
python3 scripts/wechat_article_crawler/wechat_crawler.py --drain-analysis-queue
```

## Implementation Strategy

### Scheduling Refactor

当前 `_schedule_async_job(...)` 在 `process` 模式下会立即写 job 文件并启动子进程。

本次建议拆成两类语义：

1. 自动解读队列语义
   - 只写 job 文件
   - 不立即起子进程

2. 需要即时执行的其他异步语义
   - 继续保留现有即时子进程模式

这样可以把改动范围限制在自动解读相关入口，避免误伤其他异步能力。

### Queue Drain Entry

建议在 `wechat_crawler.py` 增加一个显式入口，例如：

- `--drain-analysis-queue`

其内部职责应包括：

- 获取全局锁
- 扫描 job 文件
- 选择可运行任务
- 执行单篇解读
- 更新 job 状态
- 输出摘要日志

## Testing Strategy

建议新增或补充以下测试：

1. 入队测试
   - 新文章会被写成 `pending` job
   - 同一 `article_id` 不会重复入队

2. 队列消费测试
   - worker 只处理 `pending` 和到期的 `retry_waiting`
   - `running` 或未到期任务不会被重复消费

3. 失败状态测试
   - 可恢复失败会转成 `retry_waiting`
   - 外部失败会转成 `failed_external`

4. 历史失败回收测试
   - 历史失败任务能被 worker 再次拾取
   - 主抓取再次遇到同文时，失败 job 能恢复为可处理状态

5. 互斥测试
   - 重复启动队列 worker 时，不会并发消费同一批任务

## Validation

需要验证以下内容：

1. 主抓取按原有固定时刻正常执行，不受影响
2. 主抓取后新文章只入队，不立即执行自动解读
3. 手工运行 `--drain-analysis-queue` 能正常消费 `pending` 任务
4. 成功任务能继续写入 `output/article_analysis/` 并刷新聚合页
5. 可恢复失败任务能进入 `retry_waiting` 并在后续被重试
6. 外部失败任务不会在每轮 worker 中无限重试
7. 每 30 分钟 `launchd` worker 能按计划触发
8. worker 与主抓取不会发生重复消费或重入冲突

## Risks And Mitigations

### Risk 1: job 状态复杂后出现重复消费

缓解：

- 使用全局锁保证单次只有一个 worker 运行
- job 落盘更新时先写 `running` 再执行
- 以 `article_id` 做强去重

### Risk 2: 历史失败任务不断堆积

缓解：

- 区分 `retry_waiting` 和 `failed_external`
- 只有可恢复失败才进入周期性重试
- 对外部失败保留记录但不无限自动重跑

### Risk 3: 改造调度层时误伤现有即时异步能力

缓解：

- 只对“自动解读”入口切换为入队模式
- 保持其他异步行为继续沿用现有逻辑
- 用测试锁定自动解读与其他异步任务的分支语义

## Success Criteria

以下结果同时满足时，本次设计视为成功：

1. 主抓取固定时刻保持不变
2. 抓取与自动解读执行成功解耦
3. 新文章与历史失败文章都进入统一自动解读队列
4. 30 分钟 worker 能稳定消费队列并生成现有解读产物
5. 主触发窗口内的自动解读压力明显低于改造前
