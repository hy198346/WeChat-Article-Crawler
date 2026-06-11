# Ollama Article Analysis Design

## Goal

在每次抓取微信公众号文章后，自动生成 AI 解读，并同时输出：

- 单篇文章简版摘要
- 本轮抓取结果的汇总解读

该能力必须是可选增强功能，不能影响现有抓取、Markdown 保存、Server 酱推送和去重逻辑的稳定性。

## Scope

本次设计覆盖以下场景：

- `--push-latest-all` 批量检查公众号更新后，对本轮新文章生成单篇解读与批量汇总
- `--extract-latest` 抓取单个公众号最新文章后生成单篇解读
- `--article-url` 直接解析单篇文章时生成单篇解读

本次不包含：

- 多轮对话式问答
- 向量检索或知识库
- 长文分段总结和多次迭代压缩
- 异步任务队列或独立分析服务

## Current Context

当前项目已经具备以下能力：

- 抓取微信公众号文章列表并提取正文
- 将文章保存为 Markdown
- 将新文章以单篇或汇总方式推送到 Server 酱
- 在批量模式下依据 `push_state.json` 做去重

适合的接入点已经存在于主流程中：

- `run_push_latest_all()` 负责本轮批量抓取、保存和推送
- `run_extract_latest()` 负责单篇公众号最新文章提取
- `run_extract_from_url()` 负责单篇链接解析

## Requirements

### Functional Requirements

1. 每篇成功抓取的文章都可以生成一份简版 AI 解读。
2. 批量模式下，本轮所有新文章完成单篇解读后，再生成一份批量汇总解读。
3. 解读结果既可以参与推送，也可以落盘保存。
4. 当 Ollama 不可用、超时或返回异常格式时，抓取主流程继续执行。
5. 已经存在的文章解读结果默认可复用，避免重复调用模型。

### Non-Functional Requirements

1. 默认行为应尽量保持和当前项目一致，AI 解读为可配置增强能力。
2. 配置项命名清晰，便于后续切换模型或关闭功能。
3. 输出目录结构化，和现有 `output/`、`logs/` 风格一致。
4. 失败时给出明确状态和错误信息，便于 watchdog 或人工排查。

## Recommended Approach

采用“抓取后立即解读”的同步增强方案。

理由如下：

- 与当前主流程最贴合，改动集中在已有抓取结果成型之后
- 单篇和批量汇总都可以直接复用当前 payload 结构
- 不需要新增独立服务或复杂调度
- 失败隔离简单，最容易做到“分析失败但抓取成功”

## Architecture

新增一个轻量分析层，位于“正文提取完成”与“推送/落盘”之间。

建议新增以下逻辑单元：

- `build_article_analysis_input(...)`
  - 从文章元信息和正文 Markdown 提取模型输入
  - 清洗冗余 Markdown、图片链接和过长内容
- `call_ollama_chat(...)`
  - 负责请求 Ollama `/api/chat`
  - 统一处理超时、网络异常和返回格式
- `analyze_single_article(...)`
  - 生成单篇结构化摘要
  - 支持命中缓存时直接返回
- `summarize_analysis_batch(...)`
  - 基于本轮单篇摘要生成批量汇总
- `persist_analysis_outputs(...)`
  - 将单篇和批量汇总结果写入 `output/`

## Data Flow

### Single Article

1. 主流程拿到文章标题、公众号、发布时间、URL 和正文 Markdown。
2. 构建模型输入，保留必要上下文并截断超长正文。
3. 调用 Ollama，要求输出固定结构。
4. 解析为标准分析对象。
5. 将分析对象挂到当前文章 payload。
6. 根据配置决定是否推送、是否落盘。

### Batch Mode

1. 批量模式完成所有新文章的单篇解读。
2. 抽取每篇的简版字段，而不是再次发送整篇正文。
3. 调用 Ollama 生成本轮汇总解读。
4. 将汇总结果加入批量返回 payload 和推送内容。
5. 将汇总结果保存到批次文件。

## Configuration

建议在 `config.json` 中新增以下配置项：

```json
{
  "analysis_enabled": true,
  "analysis_base_url": "http://192.168.9.158:11434",
  "analysis_model": "qwen2.5-coder:14b-cpu",
  "analysis_timeout_seconds": 30,
  "analysis_max_chars": 8000,
  "analysis_push_single": true,
  "analysis_push_batch": true,
  "analysis_save_json": true,
  "analysis_save_markdown": true,
  "analysis_skip_if_exists": true
}
```

配置含义：

- `analysis_enabled`：总开关
- `analysis_base_url`：Ollama 服务地址
- `analysis_model`：使用的模型名
- `analysis_timeout_seconds`：模型调用超时
- `analysis_max_chars`：传给模型的正文最大字符数
- `analysis_push_single`：是否在单篇结果中附加 AI 解读
- `analysis_push_batch`：是否在批量结果中附加本轮汇总
- `analysis_save_json` / `analysis_save_markdown`：是否保存结构化结果和可读结果
- `analysis_skip_if_exists`：命中缓存文件时是否跳过重复分析

## Prompt Design

### Single Article Prompt

单篇解读要求模型输出固定字段：

- `topic`
- `core_points`
- `audience`
- `risks`

约束：

- 使用中文
- 避免空泛套话
- 保持简洁，适合推送
- 如果正文信息不足，应明确标记信息有限，而不是编造

### Batch Prompt

批量汇总基于单篇解读结果，输出固定字段：

- `batch_focus`
- `shared_themes`
- `priority_reads`

约束：

- 不重复粘贴单篇全文
- 重点突出本轮最值得看的文章和原因
- 长度控制在适合 Server 酱推送的范围内

## Output Layout

遵循现有目录结构化偏好，新增以下输出目录：

```text
output/
  article_analysis/
    <article_id>.json
    <article_id>.md
  article_batches/
    <batch_id>.json
    <batch_id>.md
```

命名建议：

- `article_id`：基于文章 URL 或 `title + published_at + account` 的哈希
- `batch_id`：本轮运行时间戳，例如 `20260611_213000`

## Payload Changes

### Single Article Payload

在现有文章 payload 中新增：

```json
{
  "analysis": {
    "status": "ok",
    "topic": "…",
    "core_points": ["…"],
    "audience": "…",
    "risks": ["…"]
  }
}
```

失败时：

```json
{
  "analysis": {
    "status": "skipped",
    "reason": "ollama_timeout"
  }
}
```

### Batch Payload

在 `run_push_latest_all()` 的输出对象中新增：

```json
{
  "batch_analysis": {
    "status": "ok",
    "batch_focus": "…",
    "shared_themes": ["…"],
    "priority_reads": ["…"]
  }
}
```

## Push Rendering

### Single Push

在现有推送正文后追加短段落：

- `AI解读`
- `主题`
- `核心观点`
- `适合谁看`
- `风险/注意点`

### Batch Push

在现有批量推送末尾追加：

- `本轮解读`
- `本轮重点`
- `共性观点`
- `优先阅读`

如果模型失败，则不追加 AI 解读段落。

## Caching and Idempotency

为避免重复调用模型：

- 默认按 `article_id` 检查 `output/article_analysis/` 中是否已有结果
- 命中有效结果时直接复用
- 批量汇总按 `batch_id` 保存，不做跨批次复用

该策略与当前抓取去重互补：

- 抓取去重决定“是否有新文章”
- 分析缓存决定“是否需要重新请求模型”

## Error Handling

错误处理原则是“分析失败不影响抓取成功”。

需要覆盖的情况：

- Ollama 服务不可达
- 请求超时
- HTTP 非 200
- 返回体不是预期 JSON
- 模型输出缺少字段
- 正文为空或内容不足

处理方式：

- 单篇分析失败时，当前文章写入 `analysis.status=skipped` 和失败原因
- 批量汇总失败时，只跳过汇总，不回滚单篇结果
- 所有错误都写入标准输出，必要时补充到 `logs/` 或现有任务日志

## Testing Strategy

建议只补高价值测试：

1. 单篇分析成功
   - mock Ollama 成功响应
   - 校验 payload 挂载和文件落盘
2. 单篇分析失败
   - mock 超时或异常响应
   - 校验主流程继续执行且写入 `status=skipped`
3. 批量汇总成功
   - 基于多篇单篇摘要生成批量汇总
4. 缓存命中
   - 已存在分析文件时不重复请求模型

手动验证顺序：

1. `--article-url` 验证单篇分析
2. `--extract-latest` 验证单公众号主流程
3. `--push-latest-all` 验证批量汇总和推送展示

## Implementation Notes

实现时应优先保持文件边界清晰。

建议将 Ollama 调用与提示词逻辑封装为独立模块，例如：

- `article_analysis.py`

`wechat_crawler.py` 只负责：

- 组织调用
- 传递文章上下文
- 接收分析结果并写入 payload

这样可以避免继续膨胀主文件，也便于测试。

## Open Decisions Resolved

以下设计已确认：

- 同时生成单篇解读和批量汇总
- 默认采用适合推送的简版摘要
- 解读结果写入 `output/` 下的结构化目录
- 分析失败不阻断抓取主流程

## Acceptance Criteria

满足以下条件时视为设计目标完成：

1. 批量抓取新文章后，每篇文章都能得到一份可选的 AI 解读。
2. 批量模式下可以生成一份本轮汇总解读。
3. 单篇与批量解读都可以配置是否参与推送。
4. 解读结果会按结构化目录保存到 `output/`。
5. Ollama 异常不会导致抓取任务失败。
6. 命中已有分析结果时不会重复请求模型。
