# WeChat-Article-Crawler Ollama Schema Drift Logging 设计

## Goal

为“本地模型解读”增加最小可检索日志，帮助定位局域网 Ollama 返回 schema 漂移后为何被判成 `empty_analysis`。

本次设计只解决一件事：

- 当 `provider=ollama` 的单篇分析最终落成 `empty_analysis` 时，日志中要能直接看出原始返回更像哪种 schema、哪些字段有值、解析器为什么没有认出来。

## Scope

本次覆盖：

- 在单篇本地模型分析链路中，为 `empty_analysis` 失败增加一条结构化调试日志
- 日志包含 article 维度上下文、候选字段命中情况和原始返回摘要
- 为日志内容补 focused 测试

本次不覆盖：

- 不对所有成功请求都打 schema 日志
- 不把原始返回全文落盘到 `output/`
- 不改现有分析结果数据结构
- 不引入新的日志库或埋点系统

## User Problem

最近已经出现过真实案例：

- 局域网 Ollama 没有超时，也确实返回了 JSON
- 但返回字段不是当前主 schema，而是诸如 `content`、`key_points`、`trend_impact` 或 `core_trend`、`application_types`、`platform_response`
- 旧代码把这种结果判成 `empty_analysis`

虽然本轮已经补了 schema 兼容，但后续模型输出仍可能继续漂移。需要在再次失败时，日志能直接告诉我们：

- 当时返回里有哪些 key
- 哪些候选字段是非空的
- 原始文本前缀是什么

这样后续排查不需要再人工复现多轮。

## Options Considered

### Option A: 只在 `empty_analysis` 时打结构化日志

做法：

- 仅当本地模型链路最终返回 `{"status": "skipped", "reason": "empty_analysis"}` 时打印一条日志

优点：

- 日志噪音最低
- 直接命中当前问题
- 风险最小，不影响正常成功链路

缺点：

- 看不到“成功但 schema 已经漂移”的样本

### Option B: 每次本地模型解析后都打印 schema 摘要

优点：

- 样本更多，便于长期观察漂移趋势

缺点：

- 日志量明显变大
- 正常路径也会产生持续噪音

### Option C: 把原始返回全文落盘到 `output/debug`

优点：

- 调试证据最完整

缺点：

- 运行产物增多
- 清理和隐私边界更复杂

## Recommended Approach

推荐采用 Option A。

原因：

- 正好覆盖当前最痛的失败场景
- 能在不扩大日志面的前提下，快速锁定 schema 漂移
- 如果后续仍高频遇到新漂移，再升级为 Option B 也不晚

## Design

### Trigger Point

在 `article_analysis.analyze_single_article()` 的本地模型分支中增加日志。

触发条件：

- 当前请求实际走的是本地 Ollama
- 最终结果是 `status = skipped`
- 且 `reason = empty_analysis`

仅在同时满足以上条件时打印日志。

### Log Shape

建议日志前缀固定为：

```text
[ollama-schema-drift]
```

建议日志字段：

- `provider`
- `account`
- `article_id`
- `title`
- `reason`
- `top_level_keys`
- `non_empty_candidates`
- `raw_preview`

字段含义：

- `provider`：固定为 `ollama`
- `account`：公众号名，便于直接 grep
- `article_id`：文章唯一标识，便于和缓存文件对应
- `title`：文章标题，便于人工识别
- `reason`：固定为 `empty_analysis`
- `top_level_keys`：原始 JSON 顶层 key 列表
- `non_empty_candidates`：在候选字段中实际有值的字段名列表
- `raw_preview`：原始返回压缩后的前 300~500 个字符

### Candidate Fields

`non_empty_candidates` 至少覆盖以下字段：

- `summary`
- `content`
- `analysis`
- `text`
- `result`
- `key_points`
- `core_points`
- `trend_impact`
- `key_impact`
- `core_trend`
- `platform_response`
- `application_types`

判断规则：

- 使用现有标准化 helper 做“是否有意义内容”的判断
- 避免把空字符串、空数组、空对象记成命中

### Raw Preview Rules

`raw_preview` 规则：

- 优先使用原始模型返回文本，而不是重新序列化后的对象
- 去掉换行和多余空白，压成单行
- 截断到固定上限，避免日志爆长

建议上限：

- 400 字符

### Failure Safety

日志本身必须是 best-effort：

- 即使预览提取失败、JSON key 读取失败，也不能打断原有返回
- 如果内部提取异常，最多降级成更短的错误提示，不影响主流程

## Testing

补一条 focused 测试即可：

- 构造本地模型返回无法被当前字段映射识别的 JSON
- 触发 `empty_analysis`
- 断言 stdout 中包含：
  - `[ollama-schema-drift]`
  - `reason=empty_analysis`
  - 文章标题或 article_id
  - 至少一个候选字段名

测试目标不是锁死完整日志格式，而是锁住“失败时一定会留下足够诊断信息”。

## Verification

实现完成后验证：

1. focused `pytest`
2. 全量 `pytest -q`
3. 人工触发一次本地模型 `empty_analysis` 场景，确认日志可 grep

## Risks And Mitigations

### Risk 1: 日志里原始返回过长

缓解：

- 固定 `raw_preview` 截断长度
- 压成单行

### Risk 2: 打印过多无关字段

缓解：

- 只记录固定候选字段名
- 只在 `empty_analysis` 时打印

### Risk 3: 日志格式过于刚性，后续不好扩展

缓解：

- 测试只校验关键片段，不锁死整行字符串
- 保留新增字段空间
