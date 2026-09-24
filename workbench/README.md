# 海南康养视频工作台

输入文案后默认制作 9:16、1080p 成片；查看真实任务日志，播放和下载检查通过的视频。成片后可从实际使用的镜头抽取 10 张封面候选，选择一张并填写片头 0.5 秒的大黄字，再填写一次整片固定的上白下黄标题。改字会立刻更新原片上的浏览器预览，并将草稿保存在该任务中；点击下载时才生成新的 MP4。原素材、向量库和生成仍在 NAS。该网页后台可部署到新加坡服务器，通过已建立的 Tailscale 私网连接 NAS。

## 本机运行

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m workbench --nas-config data/nas-deploy/client.json
```

打开 `http://127.0.0.1:8788/`。本机模式仅监听回环地址；保留现有 NAS 局域网配置。主音色试听使用 `reference/speaker-reference.mp3`，与原技能音色文件一致。公开仓库不包含个人音频或 `data/nas-deploy/client.json`，首次克隆请按[根目录说明](../README.md)自行配置。生产部署使用下方密码登录配置。

本机数据保存在 `data/workbench/workbench.sqlite3`，包括文案、任务标识与最近状态。草稿单独保存在使用者当前浏览器；清理浏览器数据会删除草稿，不会删除已提交的任务。

已完成、失败、中断或被拒绝的记录，在工作台记录该结束状态后保留 72 小时。启动、健康检查及读取任务记录时自动清理到期记录；刷新状态不会延长保留时间，续作后重新计时。排队、运行及等待核对的任务继续保留。清理只删除工作台记录，不删除 NAS 素材、成片、音色文件或 COS 音乐。

## 新加坡部署

使用 Zeabur 托管时请改看 [Zeabur 部署说明](ZEABUR.md)；下面的 Docker Compose/Caddy 步骤用于自己管理的服务器。

准备一个指向新加坡服务器的域名，确认服务器 Tailscale 在线且能访问 `http://100.104.108.83:8780/health`。下面操作只部署网页与转发后台，不复制或重新向量化素材。

1. 把整个 `workbench/` 目录上传到服务器，例如 `/opt/hainan-workbench`。
2. 单独放入或保留 `reference/speaker-reference.mp3`，这是主音色试听文件，仅登录后可读取。公开仓库不包含此私人文件；NAS 使用的生成音色不变，缺少这个文件只会禁用网页试听。
3. 复制 `.env.example` 为 `.env`，填写域名、NAS 已有 `MIXER_API_TOKEN` 对应的 `NAS_TOKEN`，以及至少 12 位的工作台登录密码。不要把这些值贴进聊天或前端代码。建议用服务器编辑器填写，再执行 `chmod 600 .env`。
4. 确认服务器 80、443 端口可用于该站点；若已运行其他站点，复用现有反向代理，不直接启动这里的 Caddy 占用相同端口。此方案需要已安装 Docker Engine 与 Compose。
5. 在工作台目录执行 `docker compose up -d --build`。Caddy 为已解析到该服务器的域名申请 HTTPS 证书。

必须从应用容器检查实际到 NAS 的连接，不能仅凭宿主机 curl 成功：

```bash
docker compose exec workbench python -c 'from workbench.server import Settings,Nas; n=Nas(Settings.from_env()); r=n.request("GET","/health"); print(r.status_code,r.json())'
```

浏览器打开 `https://你的域名`，输入工作台密码。前端通过同源网页后台请求，访问者无需安装 Tailscale；只有新加坡服务器需要连接到 NAS 私网。服务密钥保留在网页后台，NAS 没有增加公网端口。

## 任务恢复与验证

- 正常提交始终使用同一个持久保存的 `Idempotency-Key`。提交超时会标为“等待核对”，点击“核对提交”会复用原请求。
- NAS 明确拒绝请求时显示原因。生成失败或服务重启中断不会自动重下模型订单；先核对 NAS 中已有任务与输出。
- 关闭或刷新网页不会取消 NAS 任务。点击“新建视频”会打开空白文案，旧任务继续处理；新任务各自保存进度，NAS 按队列依次制作。网页重新打开后从制作记录继续查询。
- 仅 NAS 标为 `done` 的视频开放播放和下载；视频流支持 Range，允许拖动进度条。查看检查报告可追溯实际导出文件的 SHA256。
- 封面与整片固定标题在页面内即时预览并按任务保存，不触发 NAS 转码。点击下载时，NAS 对已通过检查的原始成片生成带新文字的 MP4，不重复提交配音和配乐；新版成片再次通过 Gemini 画面与声音检查后才替换下载文件。
- 工作台只在 NAS `/health` 声明 `cover_editor` 能力后显示该入口，部署时应先更新 NAS 容器，再发布工作台；否则旧版 NAS 上的编辑入口不会误导用户。
- 网页记录只包含经本工作台提交或明确导入的任务，不会自动扫描 NAS 内所有历史任务。

导入一条真实已完成任务用于验收，不生成新视频：

```powershell
.\.venv\Scripts\python.exe -m workbench --nas-config data/nas-deploy/client.json --import-job fbd9868c206247e89938560dce3793ca
```

## 第一版边界

单工作区、一个共享登录密码，适合当前负责人或可信团队。没有多租户账户、按客户隔离素材、在线换音色、素材上传自动索引或单独素材搜索功能。新增素材仍通过已有索引流程更新；不会声称上传即能匹配。

本机运行与 NAS 联调不等于新加坡已上线。远程上线还需配置实际服务器目录、域名、HTTPS 与登录密码，并从真实部署容器验证。

## 测试

项目根目录运行 `python -m unittest discover -s tests -p test_workbench.py -v`。测试覆盖登录、跨站请求、参数校验、提交响应丢失、进程重启恢复、离线状态、视频 Range 和文件访问限制。NAS 模型生成流程仍复用原技能及其测试。
