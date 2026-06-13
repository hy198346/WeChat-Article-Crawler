# WeChat-Article-Crawler 解读页面与重解读服务 launchd 常驻化设计

## Goal

将当前手工启动的两个本地服务改为 macOS `launchd` 常驻服务托管：

- `8765`：静态页面服务，用于浏览 `output/article_analysis/index.html`
- `8766`：重解读接口服务，用于页面按钮调用 `POST /api/reanalyze`

目标是让这两个服务在本机上自动常驻、开机或加载后自动恢复，并统一纳入仓库内的 `launchd` 配置和日志体系，避免每次进入 Trae 后都要手工开两个终端。

## Scope

本次覆盖：

- 为 `8765` 静态页服务新增专用 `launchd` 配置
- 为 `8766` 重解读 API 服务新增专用 `launchd` 配置
- 新增对应启动脚本，统一处理工作目录、环境变量和日志目录
- 将服务安装到 `~/Library/LaunchAgents/`
- 通过 `launchctl bootstrap` / `kickstart` 启动并验证服务
- 停掉当前手工终端启动的两个服务，由 `launchd` 接管

本次不覆盖：

- 不改现有 `com.wechat.articlecrawler.runproject` 抓取定时任务
- 不改现有 `watchdog` 的 `launchd` 任务
- 不引入 `nginx`、`pm2`、`supervisord` 等额外常驻进程管理器
- 不改变 `8765` / `8766` 端口口径
- 不做远程访问或反向代理暴露

## Current Context

当前仓库已有：

- `config/launchd/com.wechat.articlecrawler.runproject.plist`
- `config/launchd/com.wechat.articlecrawler.watchdog.plist`
- `bin/run_project_launchd.sh`

说明项目已经有明确的 `launchd` 落地方式与日志目录约定。

当前缺口：

- `8765` 静态服务目前靠手工运行 `python3 -m http.server 8765`
- `8766` 重解读服务目前靠手工运行 `python3 scripts/wechat_article_crawler/wechat_crawler.py --serve-reanalyze`
- 重启 Trae 或系统后，这两个服务都需要重新手工拉起

## Requirements

### Functional Requirements

1. 系统应新增两个独立的 `launchd` 服务标签：
   - `com.wechat.articlecrawler.analysis-static`
   - `com.wechat.articlecrawler.reanalyze-api`
2. 静态服务应在仓库 `output/` 目录下监听 `8765`。
3. 重解读服务应启动 `wechat_crawler.py --serve-reanalyze` 并监听 `8766`。
4. 两个服务都应在 `launchd` 加载后自动拉起。
5. 两个服务都应在异常退出后由 `launchd` 自动重启。
6. 两个服务都应将 `stdout/stderr` 写入仓库 `logs/` 目录。
7. 安装完成后，系统应把仓库内的 `plist` 同步到 `~/Library/LaunchAgents/` 并实际加载。
8. 切换完成后，应停止当前手工启动的两个旧进程，避免与 `launchd` 实例冲突。

### Non-Functional Requirements

1. 继续沿用仓库现有 `launchd` 目录结构与命名风格。
2. 启动脚本应支持读取仓库根目录 `.env`，与现有脚本口径一致。
3. 失败日志应可直接从仓库 `logs/` 查看，不要求用户去系统日志里手找。
4. 不要求 root 权限，使用当前用户的 `LaunchAgents` 即可。

## Options Considered

### Option A: 保持手工终端启动

优点：

- 零改动

缺点：

- 每次都要人工拉起
- 终端关闭后服务消失
- 不满足“常驻服务”目标

### Option B: 用 `launchd` 新增两个专用常驻服务

优点：

- 完全符合 macOS 本机常驻服务习惯
- 仓库里已有 `launchd` 基础设施，可直接复用
- 可由 `launchctl kickstart` 做统一重启

缺点：

- 需要维护两个额外的 `plist` 和启动脚本

### Option C: 用单一 shell 脚本同时托管两个服务

优点：

- `plist` 数量少

缺点：

- 两个服务生命周期耦合
- 任一子进程异常退出时行为更难控制
- 日志与重启定位不如分拆清晰

## Recommended Approach

推荐采用 Option B：用 `launchd` 新增两个专用常驻服务。

原因：

- 与当前仓库已有的 `launchd` 模式最一致
- 每个服务独立启动、独立日志、独立重启，定位最清楚
- 符合用户“8765 和 8766 都用 launchd 常驻服务”的直接诉求

## File Layout

新增或修改的目标文件：

- `config/launchd/com.wechat.articlecrawler.analysis-static.plist`
- `config/launchd/com.wechat.articlecrawler.reanalyze-api.plist`
- `bin/run_analysis_static_launchd.sh`
- `bin/run_reanalyze_api_launchd.sh`

安装目标：

- `~/Library/LaunchAgents/com.wechat.articlecrawler.analysis-static.plist`
- `~/Library/LaunchAgents/com.wechat.articlecrawler.reanalyze-api.plist`

日志目标：

- `logs/launchd.analysis-static.out.log`
- `logs/launchd.analysis-static.err.log`
- `logs/launchd.reanalyze-api.out.log`
- `logs/launchd.reanalyze-api.err.log`

## Service Design

### 1. 静态页面服务

标签：

- `com.wechat.articlecrawler.analysis-static`

职责：

- 切换到 `output/` 目录
- 启动 `python3 -m http.server 8765`

建议配置：

- `RunAtLoad = true`
- `KeepAlive = true`
- `WorkingDirectory = <repo>/output`

### 2. 重解读 API 服务

标签：

- `com.wechat.articlecrawler.reanalyze-api`

职责：

- 切换到仓库根目录
- 读取 `.env`
- 启动 `python3 scripts/wechat_article_crawler/wechat_crawler.py --serve-reanalyze`

建议配置：

- `RunAtLoad = true`
- `KeepAlive = true`
- `WorkingDirectory = <repo>`

## Startup Script Rules

两个启动脚本都应：

1. 使用仓库根目录为锚点推导路径
2. 自动创建 `logs/` 目录
3. 读取默认 `.env`：
   - 若 `WECHAT_ENV_FILE` 未设置且仓库根目录存在 `.env`，则使用它
4. 避免依赖交互 shell

其中 `run_reanalyze_api_launchd.sh` 需额外保证：

- `wechat_crawler.py` 进程能继承 `.env` 中的 `LOCAL_LLM_BASE_URL`、`LOCAL_LLM_MODEL` 等配置

## Installation Flow

推荐安装流程：

1. 在仓库内写好 `plist` 与启动脚本
2. 复制 `plist` 到 `~/Library/LaunchAgents/`
3. 若旧标签已加载，先 `bootout`
4. 执行 `launchctl bootstrap gui/$UID ...`
5. 执行 `launchctl kickstart -k gui/$UID/<label>`
6. 验证端口监听：
   - `8765`
   - `8766`
7. 验证页面与接口可用
8. 停掉当前手工终端运行的两个旧进程

## Validation

需要验证以下内容：

1. `launchctl print gui/$UID/com.wechat.articlecrawler.analysis-static` 可正常输出服务状态
2. `launchctl print gui/$UID/com.wechat.articlecrawler.reanalyze-api` 可正常输出服务状态
3. `lsof -nP -iTCP:8765 -sTCP:LISTEN` 可见监听
4. `lsof -nP -iTCP:8766 -sTCP:LISTEN` 可见监听
5. 打开 `http://localhost:8765/article_analysis/index.html` 页面正常
6. `8766` 接口可正常响应浏览器页面中的“重新解读”调用
7. 停掉手工终端后服务仍可继续工作

## Risks And Mitigations

风险：

- 旧手工终端进程与 `launchd` 新实例抢占相同端口
- `.env` 未正确加载，导致 `8766` 重解读服务启动后仍拿不到局域网 LLM 配置
- 用户态 `LaunchAgents` 未正确 `bootstrap`，看似有文件但服务未实际运行

缓解：

- 切换时明确停掉现有手工进程
- 启动脚本统一复用 `.env` 加载逻辑
- 通过 `launchctl print`、端口监听和浏览器 smoke 三重验证

## Acceptance Criteria

满足以下条件即视为完成：

- `8765` 和 `8766` 都由 `launchd` 托管，而不是依赖手工终端
- 对应 `plist` 已落地到仓库和 `~/Library/LaunchAgents/`
- 两个服务均能在当前用户下自动启动并异常重启
- 页面访问和重解读接口均保持可用
- 当前手工启动的两个旧进程已被 `launchd` 接管替代
