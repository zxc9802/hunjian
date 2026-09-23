# Zeabur 部署

工作台是 Python/FastAPI 服务，必须运行后台进程。仅托管 HTML 或把仓库作为静态网站发布，会出现首页和 `/api/session` 的 404。

## 构建与入口

1. Zeabur 选择 `zxc9802/hunjian` 仓库的 `main` 分支，根目录保持仓库根目录（留空或 `/`）。
2. 根目录的 `Dockerfile` 会启动 `python -m workbench --host 0.0.0.0 --port 8788`。部署构建应显示 Docker，而非 Static。
3. 不设置静态输出目录。若之前手动设置了 `ZBPACK_OUTPUT_DIR`、`ZBPACK_IGNORE_DOCKERFILE`、自定义启动命令或构建命令，移除这些覆盖项，让 Dockerfile 生效。
4. 绑定工作台域名到 HTTP 服务端口 **8788**。保留 Zeabur 自带网关，不在此服务额外启动仓库内的 Caddy/Compose。

如果服务根目录已经设为 `workbench`，也可以保留，该目录内自带的 Dockerfile 使用对应构建上下文。不要把根目录设为 `workbench/static`。

Zeabur 官方说明：[Dockerfile 部署](https://zeabur.com/docs/en-US/deploy/methods/dockerfile)、[根目录设置](https://zeabur.com/docs/en-US/deploy/config/root-directory)。Zeabur 不直接部署 Docker Compose YAML；仓库里的 Compose 用于自行管理服务器的部署方式。

## 环境变量

在 Zeabur 服务的环境变量中配置，真实密码和令牌不要提交到 GitHub：

| 名称 | 设置 |
| --- | --- |
| `PUBLIC_URL` | `https://hunjian.qycm.top`，必须与实际访问的 HTTPS 域名一致 |
| `NAS_URL` | `http://100.104.108.83:8780`，前提是该服务运行环境已能访问这个 Tailscale 地址 |
| `NAS_TOKEN` | 现有 NAS 的 `MIXER_API_TOKEN`，原样使用 |
| `WORKBENCH_PASSWORD` | 自行设置的工作台登录密码，至少 12 位 |
| `WORKBENCH_DATA` | `/data`（镜像默认值） |
| `COS_SECRET_ID` | 腾讯 COS 访问密钥 ID，仅存在 Zeabur 服务环境变量中 |
| `COS_SECRET_KEY` | 腾讯 COS 访问密钥 Key，仅存在 Zeabur 服务环境变量中 |

`WORKBENCH_DOMAIN` 只供 Compose 中的 Caddy 使用；Zeabur 模式应直接设置 `PUBLIC_URL`。缺少有效的 NAS 配置或登录配置时程序会拒绝启动，不能通过删除校验来绕过。

工作台音乐库使用私有桶 `hunjian-1410143389`、新加坡地域 `ap-singapore`。用户登录后上传 MP3、WAV 或 M4A（单文件上限 100 MB），页面列出已上传曲目，每首支持试听、暂停和拖动进度，并可在创建任务时选择。试听通过登录保护的工作台接口读取私有桶。NAS 的 `private/runtime.env` 也必须设置相同的两个 `COS_SECRET_*` 变量，才能按任务曲目从 COS 下载；不上传或不选曲时可正常制作，保留口播，不添加背景音乐。不再需要音乐生成服务或其密钥。请给这组 COS 密钥仅授予该桶音乐库前缀所需的列出、上传与下载权限。本次功能更新需要同时部署工作台和 NAS 服务。

## 任务数据与 NAS 连接

在正式创建任务前，为 `/data` 配置持久存储，并确认 UID 10001 可写。这里保存网页任务记录和会话，源素材仍留在 NAS。若目录已有数据，先备份再挂载，避免覆盖。参见 [Zeabur Volumes](https://zeabur.com/docs/en-US/operations/data/volumes)。

在新加坡服务器宿主机安装 Tailscale 不等于 Zeabur 容器一定可达 NAS。应在服务命令行中实际执行：

```sh
python -c 'from workbench.server import Settings,Nas; n=Nas(Settings.from_env()); r=n.request("GET","/health"); print(r.status_code,r.json())'
```

只有健康检查成功还不够，登录工作台后还需检查任务接口鉴权。若使用 Zeabur 的另一台服务器或托管集群，需要先为其建立到 NAS 的私网连接，不要直接把 NAS 8780 端口暴露到公网。

## 音色试听

GitHub 不包含私人参考音频。NAS 连接正常但“试听音色”灰色时，表示工作台尚未收到试听文件，不影响 NAS 使用自己的音色生成配音。

部署后可用工作台密码登录 `/api/login`，在同一会话中向 `PUT /api/reference/voice` 上传与 NAS 主音色一致的 MP3 原始字节：`Content-Type: audio/mpeg`，最大 5 MB；与其他写入接口一样需要 `X-Workbench-Request: 1` 和正确的 `Origin`。该接口只配置网页试听，不修改 NAS 音色。上传和播放均受现有登录鉴权保护。

文件保存到 `WORKBENCH_DATA/speaker-reference.mp3`，因此 `/data` 应挂载持久存储，避免重新部署后丢失。上传成功后刷新页面，`/api/connection` 的 `voice_available` 应为 `true`，`/api/reference/voice` 应返回 MP3 并支持 Range 播放。原 `VOICE_FILE` 配置仍作为未上传时的备用路径。

## 验收

- `https://hunjian.qycm.top/` 返回工作台登录页面。
- `/health` 返回 `{"status":"ok","service":"hainan-workbench"}`。
- `/api/session` 在未登录时返回 `authenticated: false` 和 `login_required: true`。
- `/static/style.css` 返回 CSS；`/workbench/server.py` 不应再作为文件返回。
- 登录后检查 NAS 连接、制作记录和成片下载；刷新或重新部署后历史任务仍在。

若仍返回 404，先确认运行的是最新提交、Docker 构建已完成、域名绑定了正确服务和 8788 端口。若日志显示缺少环境变量、数据库目录不可写或连接 NAS 超时，按相应错误处理；这与静态部署的 404 是不同阶段的问题。
