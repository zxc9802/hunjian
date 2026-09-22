# NAS 独立运行与任务接口

程序运行在 NAS 的 Docker 容器中。电脑只是提交任务的客户端；关电脑不会停止已经提交的 NAS 任务。NAS 自己需要保持开机和联网。新加坡服务器接入时，先建立到 NAS 的受控私网连接或 HTTPS 网关，不能直接使用新加坡机器无法访问的 `192.168.20.225`。

## 已部署的新加坡私网连接

2026-09-22，用户提供的新加坡 Ubuntu 终端截图确认，访问 `http://100.104.108.83:8780/health` 返回 `status=ok`、`service=hainan-mixer`、`worker_concurrency=1`。这验证了新加坡到 NAS 的网络和健康检查接口；尚未验证新加坡程序携带密钥提交任务、下载视频或实际传输速度。

- 新加坡设备 `hainan-agent-sg`：`100.82.206.41`。
- NAS 设备 `hainan-materials-nas`：`100.104.108.83`。新加坡程序使用 API 基址 `http://100.104.108.83:8780`；局域网客户端仍可使用原 LAN 地址。
- NAS 连接项目 `hainan-materials-network`，容器 `hainan-materials-tailscale`，配置目录为共享文件夹 `docker/hainan-mixer/tailscale`。
- 连接容器加入 Docker 网络 `hainan-mixer_default`，通过 Tailscale Serve 将私网 TCP 8780 转发到 `hainan-mixer:8780`。没有发布新的宿主机端口，也没有启用公网 Funnel、出口节点或整个局域网的路由。
- `config/serve.json` 只读挂载，`state/` 持久保存 Tailscale 设备身份；后者不能分享或打进技能包。`TS_AUTH_ONCE=true`、`TS_BOOT_TIMEOUT=30m` 用于保留登录并给首次网页确认留下时间；默认一分钟的启动等待曾导致反复生成登录链接。
- 容器使用已校验并导入的 `tailscale/tailscale:stable` 镜像，`pull_policy: never`；更新镜像需要显式重新导入或调整拉取策略。

私网内的 HTTP 流量通过 Tailscale 加密连接传输，业务 API 仍要求 Bearer 密钥。智能体运行环境必须能够访问该私网；若程序部署在另一个容器中，需从那个容器再次检查连接。不要把宿主机的健康检查成功当成程序已完成接入。

当前提供下文列出的完整混剪任务 API，尚无独立的原素材搜索、预览或片段下载接口；新增素材的自动增量索引也尚未配置。

## 部署内容

专用项目目录包含 `app/`、`seed/catalog/`、`private/runtime.env`、`data/`、`compose.yaml`。`app/` 是技能目录中的脚本、资源和 `deploy/`。`seed/catalog/` 是原库的 SQLite 一致性备份和 `proxies/`。原视频不复制。

`deploy/compose.yaml` 针对当前 NAS 配置：素材 `/volume1/海南康养素材库` 只读挂载为 `/media`；程序 UID/GID 为管理员界面显示的 `1026:10`；监听 NAS 局域网地址的 8780 端口；4 GB 内存、4 CPU 配额、单个后台工作线程，重启策略为 `unless-stopped`。迁移到其他 NAS 必须核实这些值。

绿联项目实际配置为项目目录内的 `docker-compose.yaml`。更新程序后在 Compose 配置中保留 `pull_policy: build` 再重新部署，确保执行更新后的 Dockerfile；普通容器重启不重新构建。Dockerfile 会为程序文件设置读取和目录遍历权限，随后以 `1026:10` 运行。镜像不包含服务密钥。

国内 NAS 使用 302 官方国内入口 `api.302ai.cn`。Compose 中的 `RERANK_URL`、`TTS_BASE_URL` 和 `TTS_UPLOAD_FALLBACK_URL` 分别配置重排、配音及参考音频备用上传地址；模型和已有密钥不变。国内任务仍可能返回 `file.302.ai` 的音频链接，使用国内入口时只将这一官方 CDN 主机名映射到已验证的 `file.302ai.cn`，保留原路径、查询参数和 TLS 验证，不向下载主机发送 API 密钥。不能把电脑上的代理地址作为 NAS 长期运行的依赖。302 官方域名说明见 [302 iOS 使用说明](https://help.302.ai/docs/iOS-APP)。

`private/runtime.env` 由管理员保管，含 `OPENLUX_API_KEY`、`RERANK_API_KEY`、`SUNO_API_KEY`、随机 `MIXER_API_TOKEN`。实际凭据不放入技能包、镜像、报告或客户端网页。Docker 构建上下文只指向 `app/`，不包含私有凭据目录。

启动首先用 `deploy/migrate_catalog.py` 将 Windows 素材路径映射到 `/media`，校验文件大小、修改时间和代理文件，保留原有向量数据并建立 FAISS。跨 SMB 时间精度仅容忍 100 纳秒差异，发现文件变化即停止。`data/catalog/migration-report.json` 记录向量数、维度、向量内容校验和及重新计算数。

## API

除 `GET /health` 外均要求 `Authorization: Bearer <MIXER_API_TOKEN>`。没有跨站 CORS，也不接受浏览器跨站直接调用；应由智能体后台保管密钥。

- `POST /v1/jobs`：JSON 含 `text`、`emotion_alpha`（默认 0.8）、`width`/`height`（默认 1080/1920）。必须附 `Idempotency-Key`，8–128 位英文、数字、点、下划线或短横线。同一 ID 与相同参数重复提交返回原任务，不重新下单；不同参数返回 409。
- `GET /v1/jobs/{id}`：`queued`、`running`、`done`、`failed` 或 `interrupted`，包含阶段日志。成功才返回成片和报告地址。
- `GET /v1/jobs/{id}/video`：通过 Gemini 检查的成片，支持 Range 下载。
- `GET /v1/jobs/{id}/report`：与实际视频 SHA256 绑定的检查报告。
- `GET /v1/jobs/{id}/captions`：按实际配音时间轴生成的字幕。
- `GET /v1/jobs/{id}/plan`、`GET /v1/jobs/{id}/cuts`：制作计划与实际镜头裁切记录。

上限为 20 个等待或运行中的任务，单条文案 1–6000 字符。情绪范围和步长由服务端验证。质量检查失败不会把中间视频作为成功成片开放下载。

## 客户端

将连接配置保存在工作区忽略的私有目录，JSON 只有 `base_url` 和 `token`。示例地址为 `http://192.168.20.225:8780`。不要在命令行参数或聊天中直接写 token。

```powershell
.\.venv\Scripts\python.exe video-material-match/scripts/nas_client.py --config data/nas-deploy/client.json submit --text-file script.txt --request-id a-fixed-id-for-this-video
.\.venv\Scripts\python.exe video-material-match/scripts/nas_client.py --config data/nas-deploy/client.json status <任务ID>
.\.venv\Scripts\python.exe video-material-match/scripts/nas_client.py --config data/nas-deploy/client.json download <任务ID> --output outputs/nas-result
```

提交返回网络错误时，继续使用相同 request-id；不要换 ID 盲目重发。任务处理不依赖客户端保持在线。

## 恢复与验收

SQLite 保留队列与日志。服务重启后，尚未开始的任务继续排队；运行中的任务标为 `interrupted`，保留 `data/outputs/{id}` 的模型任务记录，不自动重发可能已计费的请求。此版本尚无自动恢复中途渲染的 API，需核对已有供应商 task_id 和产物后人工恢复。恢复原任务时保留同一任务 ID 和输出目录，复用保存的计划、配音及音乐记录；保存计划的原文必须与任务文案相同，避免重新分段造成重复配音计费。只有已排除提交结果不确定的任务才可由管理员重新入队。

验收必须包括 NAS 实际读源文件、完成文字检索、指定音色配音、纯音乐混音、FFprobe/Gemini 检查及下载核验。仅 Docker 安装成功、接口返回 200 或本地单元测试通过，不能说 NAS 已完成部署。

国内 NAS 构建时从清华镜像下载 Debian 主仓库和 Python 依赖，保留 Debian 签名验证与 HTTPS 校验，Debian 安全更新仍走官方源。基础 Python 镜像来自 Docker 官方 `library/python`；NAS 直连仓库超时可导入经过镜像层摘要校验的本地归档。

官方构建参考：[Docker Compose build](https://docs.docker.com/reference/compose-file/build/)、[FastAPI 容器部署](https://fastapi.tiangolo.com/deployment/docker/)、[清华 Debian 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/debian/)、[清华 PyPI 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)。
