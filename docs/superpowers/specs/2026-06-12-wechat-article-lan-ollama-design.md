# WeChat-Article-Crawler 局域网 Ollama 复用设计

## Goal

让 `WeChat-Article-Crawler` 直接复用“研报站”已有的局域网 Ollama 接入方式，无需再手工复制本机 Ollama 配置，并让定时任务自动使用同一套配置完成：

- 文章解读生成
- `Server酱` 推送附带 AI 解读

## Scope

本次仅覆盖微信公众号文章站的 Ollama 配置来源、默认值与定时任务接入，不改动以下内容：

- 文章抓取主流程
- `Server酱` 推送渠道本身
- 企业微信推送主流程
- Ollama prompt 结构与结果格式

## Current Context

当前项目的 AI 解读能力已经具备，但默认配置仍偏向“本机 Ollama”：

- `scripts/wechat_article_crawler/article_analysis.py` 的 `DEFAULT_ANALYSIS_CONFIG.analysis_base_url` 仍是 `http://127.0.0.1:11434`
- `config.json.example` 和 README 也以本机地址为主
- 定时任务 `bin/run_project_launchd.sh` 会自动读取根目录 `.env`
- 当前定时推送已经可用，但最近日志显示 `analysis.status=skipped`，原因是 `analysis_enabled=false`

对比“研报站”现状：

- 主要通过环境变量 `LOCAL_LLM_BASE_URL` 读取局域网 Ollama 地址
- 默认值回落到 `http://192.168.9.158:11434` 或其 `/v1` 兼容路径
- 定时/脚本链路天然吃 `.env` 或环境变量，不要求手工复制 URL

## Requirements

### Functional Requirements

1. 微信文章站应优先复用研报站风格的局域网 Ollama 配置。
2. 在未显式配置本地地址时，应默认连接局域网 Ollama：`http://192.168.9.158:11434`。
3. 定时任务执行时应自动读取相同配置，不需要额外手工同步。
4. 打开 `analysis_enabled` 后，抓取结果应生成真实 AI 解读并进入 `Server酱` 推送正文。
5. Ollama 不可达时，抓取与推送主流程仍继续执行。

### Non-Functional Requirements

1. 配置优先级必须清晰、稳定、可测试。
2. 不引入新服务，不增加额外运行依赖。
3. 文档、示例配置与代码默认值保持一致。
4. 保持现有输出目录与日志口径不变。

## Recommended Approach

推荐采用“环境变量优先 + 局域网默认值兜底 + 本机配置落地”的方案。

理由：

- 最接近研报站现有方式，便于统一维护
- 定时任务已经会读 `.env`，可以无缝复用
- 保留显式配置能力，避免未来切换模型服务时受限
- 即使迁移到其他机器，也只需要配置环境变量，而不是修改代码

## Configuration Priority

`WeChat-Article-Crawler` 的 Ollama 地址读取顺序调整为：

1. `config.json.analysis_base_url`
2. 环境变量 `LOCAL_LLM_BASE_URL`
3. 环境变量 `OLLAMA_BASE_URL`
4. 默认值 `http://192.168.9.158:11434`

说明：

- `config.json` 仍保留最高优先级，便于特殊机器单独覆盖
- `LOCAL_LLM_BASE_URL` 与研报站保持一致，作为首选共享变量
- `OLLAMA_BASE_URL` 作为兼容兜底，方便其他脚本复用
- 最终默认值改成局域网地址，避免再次回落到本机未启动的 `127.0.0.1`

## Files To Change

### Code

- `scripts/wechat_article_crawler/article_analysis.py`
  - 调整默认 `analysis_base_url`
  - 在 `get_analysis_config()` 中合并环境变量来源
  - 保证返回值已经是最终可用的 base url

### Local Runtime Config

- `.env`
  - 新增或更新 `LOCAL_LLM_BASE_URL=http://192.168.9.158:11434`
- `config.json`
  - 打开 `analysis_enabled=true`
  - 如无必要，不再显式写本机 `127.0.0.1`

### Documentation

- `config.json.example`
  - 将示例地址改为局域网 Ollama
- `README.md`
  - 改成“默认走局域网 Ollama，可用环境变量复用研报站方式”的描述

### Tests

- `tests/test_article_analysis.py`
  - 更新默认 base url 断言
  - 新增环境变量优先级测试

## Runtime Flow

### Scheduled Run

1. `launchd` 触发 `bin/run_project_launchd.sh`
2. 脚本自动读取根目录 `.env`
3. `bootstrap_refresh_auth.py` / `wechat_crawler.py` 进入抓取主流程
4. `article_analysis.py` 按新优先级计算 Ollama base url
5. 成功时生成真实文章解读与批量汇总
6. 推送阶段将 AI 解读追加到 `Server酱` 正文

### Manual Run

手工执行以下命令时也会使用同一套配置优先级：

- `--article-url`
- `--extract-latest`
- `--push-latest-all`

## Error Handling

当局域网 Ollama 不可达、超时或返回异常时：

- 文章抓取继续执行
- `Server酱` 仍可发送基础文章信息
- `analysis.status` 写为 `skipped`
- 原因保持现有语义：
  - `analysis_disabled`
  - `ollama_timeout`
  - `ollama_error:*`

本次不新增复杂重试策略，也不改变 watchdog 逻辑。

## Validation Plan

### Static Validation

1. 单元测试通过：
   - 默认 base url 测试
   - 环境变量优先级测试
2. 文档与示例配置中的默认地址一致

### Runtime Validation

1. 连通性检查：
   - 实际读取到的 Ollama 地址为 `http://192.168.9.158:11434`
   - `/api/tags` 可访问
2. 单篇联调：
   - 跑一篇文章并生成真实 `analysis`
   - 在 `output/article_analysis/` 看到输出文件
3. 推送联调：
   - `Server酱` 收到带 `AI解读` 的正文
4. 定时任务联调：
   - `launchctl kickstart -k` 触发一次
   - `logs/run_project_launchd.last.log` 中看到真实解读而不是 `analysis_disabled`

## Risks

1. 局域网 Ollama 服务地址后续变更时，需要改 `.env` 或显式配置。
2. 如果其他机器没有该局域网可达性，默认值会导致 AI 解读继续跳过，但不会影响主流程。
3. 如果 `config.json` 中仍残留旧的 `analysis_base_url`，会因为配置优先级更高而覆盖环境变量；这是预期行为，需要在本机配置落地时一并清理。

## Acceptance Criteria

满足以下条件即视为完成：

1. 微信文章站默认不再依赖本机 `127.0.0.1:11434`。
2. 微信文章站可以直接复用研报站风格的 `LOCAL_LLM_BASE_URL`。
3. 定时任务无需额外人工复制配置，即可读取局域网 Ollama 地址。
4. 启用 AI 后，`Server酱` 可收到真实解读内容。
5. Ollama 不可达时，抓取与推送仍保持可用。
