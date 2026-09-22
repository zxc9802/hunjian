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

`WORKBENCH_DOMAIN` 只供 Compose 中的 Caddy 使用；Zeabur 模式应直接设置 `PUBLIC_URL`。缺少有效的 NAS 配置或登录配置时程序会拒绝启动，不能通过删除校验来绕过。

## 任务数据与 NAS 连接

在正式创建任务前，为 `/data` 配置持久存储，并确认 UID 10001 可写。这里保存网页任务记录和会话，源素材仍留在 NAS。若目录已有数据，先备份再挂载，避免覆盖。参见 [Zeabur Volumes](https://zeabur.com/docs/en-US/operations/data/volumes)。

在新加坡服务器宿主机安装 Tailscale 不等于 Zeabur 容器一定可达 NAS。应在服务命令行中实际执行：

```sh
python -c 'from workbench.server import Settings,Nas; n=Nas(Settings.from_env()); r=n.request("GET","/health"); print(r.status_code,r.json())'
```

只有健康检查成功还不够，登录工作台后还需检查任务接口鉴权。若使用 Zeabur 的另一台服务器或托管集群，需要先为其建立到 NAS 的私网连接，不要直接把 NAS 8780 端口暴露到公网。

## 验收

- `https://hunjian.qycm.top/` 返回工作台登录页面。
- `/health` 返回 `{"status":"ok","service":"hainan-workbench"}`。
- `/api/session` 在未登录时返回 `authenticated: false` 和 `login_required: true`。
- `/static/style.css` 返回 CSS；`/workbench/server.py` 不应再作为文件返回。
- 登录后检查 NAS 连接、制作记录和成片下载；刷新或重新部署后历史任务仍在。

若仍返回 404，先确认运行的是最新提交、Docker 构建已完成、域名绑定了正确服务和 8788 端口。若日志显示缺少环境变量、数据库目录不可写或连接 NAS 超时，按相应错误处理；这与静态部署的 404 是不同阶段的问题。
