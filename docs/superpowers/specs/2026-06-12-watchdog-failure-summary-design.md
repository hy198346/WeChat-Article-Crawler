# Watchdog Failure Summary Design

## Goal

为 `watchdog` 增加一个结构化失败摘要文件，方便对话流、脚本或外部监控在不解析日志的前提下快速判断当前是否存在未恢复故障。

## Scope

本次仅覆盖 `WeChat-Article-Crawler` 的 `watchdog` 失败摘要输出，不改动主任务抓取逻辑，不引入历史归档，不新增外部依赖。

## Output Contract

- 输出路径：`output/watchdog_last_failure.json`
- 输出格式：JSON
- 写入策略：仅在存在异常时写入
- 清理策略：当 `watchdog` 检测结果恢复正常时，如果该文件存在则删除

## File Schema

```json
{
  "generated_at": "2026-06-12 01:40:14",
  "status": "failed",
  "codes": ["traceback", "nonzero_exit"],
  "title": "WeChat-Article-Crawler Watchdog Failure",
  "detail": "2 issues detected",
  "issues": [
    {
      "code": "traceback",
      "title": "主任务出现异常堆栈",
      "detail": "tail_contains=Traceback file=/path/to/log",
      "auto_fix": "launchctl kickstart -k gui/501/com.wechat.articlecrawler.runproject",
      "auto_fix_result": "ok"
    }
  ]
}
```

## Field Rules

- `generated_at`
  - 使用本地时间字符串，格式与现有日志保持一致：`%Y-%m-%d %H:%M:%S`
- `status`
  - 当前固定为 `failed`
- `codes`
  - 收集本轮 `issues` 中的 `code`，去重并排序
- `title`
  - 固定为 `WeChat-Article-Crawler Watchdog Failure`
- `detail`
  - 使用简短摘要，例如 `N issues detected`
- `issues`
  - 原样保留 `watchdog` 内部已经形成的结构化问题字段：
    - `code`
    - `title`
    - `detail`
    - `auto_fix`
    - `auto_fix_result`

## Behavior

### Failure Path

当 `watchdog` 识别到 `issues` 非空时：

1. 继续保留现有通知逻辑，不改变 Server酱 / 企业微信告警行为
2. 额外写入 `output/watchdog_last_failure.json`
3. 文件内容只反映当前一次检查的异常摘要，不做历史追加

### Healthy Path

当 `watchdog` 检测结果为正常时：

1. 继续输出现有 `ok` 日志
2. 如果 `output/watchdog_last_failure.json` 存在，则删除
3. 不写入任何 `ok` 状态文件

## Implementation Notes

- 在 `watchdog.py` 中新增一个小型 helper，专门负责：
  - 序列化异常摘要
  - 写入 JSON 文件
  - 正常时删除旧文件
- helper 应保证：
  - 父目录不存在时自动创建
  - 写文件采用原子替换，避免读到半写入内容
  - 删除失败不影响 `watchdog` 主流程返回

## Testing

新增或扩展测试覆盖以下场景：

1. 当存在 `issues` 时，生成 `output/watchdog_last_failure.json`
2. 当状态恢复正常时，删除旧的失败摘要文件
3. 生成的 `codes` 为去重排序结果
4. 输出内容包含 `issues` 中的关键字段

## Risks

- 如果把摘要字段设计得过重，后续结构调整成本会变高，因此本次保持最小字段集
- 如果写文件异常传播到主流程，可能影响 watchdog 可用性，因此文件写入必须是附加能力，不应破坏主流程

## Non-Goals

- 不保存历史失败列表
- 不生成 Markdown 摘要
- 不引入单独的状态数据库或缓存目录
- 不修改现有 launchd 配置
