# 海南康养视频混剪智能体

输入文案，从已有视频素材库匹配画面，生成配音、字幕与配乐，检查成片后播放和下载。提供制作工作台、NAS 任务 API 和可复用的素材匹配技能。

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `workbench/` | 制作工作台、登录、任务记录、NAS 转发、视频预览与下载 |
| `video-material-match/scripts/` | 素材扫描、向量索引、匹配、配音、配乐、剪辑及成片检查 |
| `video-material-match/deploy/` | NAS Docker 服务、持久任务队列和索引迁移 |
| `video-material-match/references/` | 模型接口和 NAS API 说明 |
| `tests/` | 本地自动化测试 |

工作台后台通过私网调用 NAS。原素材、FAISS 索引和生成任务留在 NAS；网页关闭不会取消已提交的任务。远程部署工作台后，网页访问者无需安装 Tailscale，但运行工作台的服务器必须能连接 NAS。

制作中若语义匹配镜头不足，会从索引里补充动态画面并在计划里标记替代镜头；不会截短口播。失败或中断的任务保留阶段文件，可从工作台原任务继续。用户也可上传音乐到腾讯 COS 音乐库并在新任务中选曲；所选音乐会铺满并裁到视频结束。

## 启动工作台

需要 Python 3.12，以及已经运行并可访问的 NAS 混剪服务。

```powershell
git clone https://github.com/zxc9802/hunjian.git
cd hunjian
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r workbench/requirements.txt
New-Item -ItemType Directory -Force data/nas-deploy
```

在本机创建 `data/nas-deploy/client.json`，填写实际 NAS 地址和管理员配置的访问令牌：

```json
{
  "base_url": "http://YOUR_NAS_ADDRESS:8780",
  "token": "REPLACE_WITH_YOUR_EXISTING_NAS_API_TOKEN"
}
```

该文件已被 Git 忽略。启动：

```powershell
.\.venv\Scripts\python.exe -m workbench --nas-config data/nas-deploy/client.json
```

访问 <http://127.0.0.1:8788/>。也可双击根目录的 `启动视频工作台.cmd`。本机默认只监听回环地址；远程部署使用 HTTPS 和工作台登录密码。

## 音频、素材和索引

本公开仓库只包含程序及说明，不包含私人音色、情绪参考音频、源视频、FAISS 数据、成片或访问凭据。克隆仓库不会复制现有 NAS 上的素材库。

- **连接现有 NAS：** NAS 继续使用它原有的音色和索引，无需重新生成索引。若需工作台试听，在 `workbench/reference/speaker-reference.mp3` 放入自己的主音色文件；缺少时会禁用试听。
- **新建生成环境：** 在 `video-material-match/assets/` 放入自己的 `speaker-reference.mp3` 和 `emotion-reference.wav`；安装 FFmpeg，并按 [技能说明](video-material-match/SKILL.md)配置模型环境变量、扫描素材与建立索引。
- **部署新 NAS：** 先读 [NAS 部署与接口说明](video-material-match/references/nas-api.md)。部署模板中的路径、UID/GID 和网络地址对应原环境，迁移时必须调整。`seed/catalog/` 和 `private/runtime.env` 需要单独准备，不在仓库中。

源视频按 8 秒划片、每秒 1 帧建立多模态索引。匹配经过 FAISS 召回、rerank 和前三名视觉核验；剪辑读取原视频，不使用低清索引代理。字幕采用场景级配音时间轴。

## 部署到新加坡服务器

如果使用 Zeabur 从 GitHub 部署，按 [Zeabur 部署说明](workbench/ZEABUR.md)操作。仓库根目录已提供 Dockerfile，用来启动完整 Python 工作台，不能选择纯静态网站部署。

按 [工作台部署说明](workbench/README.md)设置域名、HTTPS、NAS 私网地址和服务端凭据，使用 Docker Compose 启动。`workbench/.env.example` 只含占位配置。实际 `.env` 不提交到 Git。

本仓库提供部署代码，不表示云服务器已上线。上线时必须从实际工作台容器验证 NAS 健康检查、业务鉴权和成片下载。

## 测试

在项目根目录安装测试及生成模块依赖后运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r workbench/requirements.txt -r video-material-match/requirements.txt httpx
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用临时数据和模型模拟，不会提交付费生成任务。真实模型与 NAS 联调需要自行配置凭据；成功导出不等于质量检查通过，最终交付以质量报告和成片 SHA256 一致为准。

## 第一版范围

目前是单工作区、共享密码的制作工作台，使用既定音色与竖屏 1080p 默认设置，支持任务状态、封面候选、逐镜头标题和成片交付。尚未提供多租户隔离、在线更换音色、独立素材搜索和上传素材自动索引。

界面产品约定见 [PRODUCT.md](PRODUCT.md)，视觉规范见 [DESIGN.md](DESIGN.md)。
