---
name: video-material-match
description: 当用户要将文案与 Z 盘或本地视频素材自动匹配、建立视频向量库、按场景混剪并配音时使用。支持 NAS 任务 API、FAISS 检索、视频核验、IndexTTS2 配音、Suno 配乐和 Gemini 成片检查。
---

# 视频混剪素材匹配

将文案拆为视觉场景，从真实视频片段中寻找匹配画面，导出字幕与混剪视频。复用 `scripts/cli.py`，不要每次重新实现检索流程。

## NAS 任务模式

用户要求在 NAS 上运行时，先读 [references/nas-api.md](references/nas-api.md)，通过 `scripts/nas_client.py` 提交任务、查询进度、下载检查通过的成片。本机连接配置保存在工作区 `data/nas-deploy/client.json`，含私有访问密钥，不要输出其内容或打进技能包。先确认 NAS `/health` 正常，不能在 NAS 失败时悄悄改成本机生成。

提交时为每个任务固定一个 `--request-id`；请求超时后复用同一 ID，避免重复配音或配乐。只有 NAS 状态为 `done` 且下载文件 SHA256 与质量报告一致，才能宣告完成。NAS 的程序、索引和成片均在 NAS 本地，客户端退出不会停止已提交任务。新加坡服务器已通过 Tailscale 验证 `http://100.104.108.83:8780/health`；实际智能体运行环境仍需验证私网连接及业务鉴权，详见 NAS 接口说明。

## 本机设置

- 工作区：`D:\海南康养素材混剪`；Python：工作区的 `.venv\Scripts\python.exe`。
- 当前带配乐与成片检查的工作台：`http://127.0.0.1:8767/`（旧 8765 服务不具备新交付流程）。
- 素材盘：`Z:\`，对应 `\\192.168.20.225\海南康养素材库`。只读源视频，缓存与导出写到工作区。
- 全量索引：工作区 `data\catalog-full`；完成校验后通过 `data\catalog\active.json` 启用，匹配自动跟随。产物：工作区 `outputs\<任务名>`。
- `OPENLUX_API_KEY` 供 embedding/视觉模型使用，`RERANK_API_KEY` 供 302 rerank/TTS 使用；可单独设置 `TTS_API_KEY`。`SUNO_API_KEY` 供 OpenLux 的 Suno 音乐接口使用，与视觉密钥分开。只从进程环境读取，禁止把实际密钥写入技能、报告或代码。
- 情绪参考必须使用 [assets/emotion-reference.wav](assets/emotion-reference.wav)，它是用户上传音频的原始副本。不要替换为接口文档的示例情绪音频。
- 主音色参考使用 [assets/speaker-reference.mp3](assets/speaker-reference.mp3)，来源为用户上传的 `9月21日.mp3`。与情绪参考音频分开上传，分别传入 speaker_audio_url 和 emotion_audio_url。更换本地音色可传 `--speaker-file`，使用外部音频可传 `--speaker-url`。
- 情绪强度默认 **0.8**，最小 **0.1**、最大 **0.85**、步长 **0.05**。CLI 和 HTTP 服务端均执行相同校验。

## 执行

以下命令在工作区执行，`<skill>` 替换为本 SKILL.md 所在目录的绝对路径。新环境先安装 Python 3.12、FFmpeg，再 `pip install -r <skill>/requirements.txt`。

```powershell
# 盘点无需 API；全量任务可重复运行，已完成切片会跳过。
.\.venv\Scripts\python.exe <skill>/scripts/cli.py scan --root Z:/
.\.venv\Scripts\python.exe <skill>/scripts/index_all.py --root Z:/ --catalog data/catalog-full --workers 16
# 另一个终端启动实时进度看板：http://127.0.0.1:8766/
.\.venv\Scripts\python.exe <skill>/scripts/progress_ui.py --catalog data/catalog-full

# 按用户原文保存 UTF-8 文案文件后匹配。每次新任务使用新的 output 目录。
.\.venv\Scripts\python.exe <skill>/scripts/cli.py match --text-file script.txt --output outputs/example
.\.venv\Scripts\python.exe <skill>/scripts/cli.py voice --plan outputs/example/plan.json --emotion-alpha 0.8
.\.venv\Scripts\python.exe <skill>/scripts/cli.py render --plan outputs/example/plan.json
# render 自动补充镜头、配乐并检查实际成片；已有配乐用 --music-file <本地音频> 复用。

# 本地页面含真实滑块，界面只使用已索引素材；索引通过上面的命令维护。
.\.venv\Scripts\python.exe <skill>/scripts/cli.py ui
```

`scripts/launch.ps1` 可在已安装依赖的本机启动页面，缺少密钥时采用隐藏输入，密钥不落盘。

## 选择与核验

1. 全量索引每个原视频按 8 秒划片，**每秒 1 帧**；短视频保留整段，不足一秒的尾片至少保留一帧。向量输入是该片段的真实代理视频，不能用文件名冒充视频 embedding。另用视觉模型生成事实描述，供文本 reranker 排序。
2. 文案切片必须保留每一个原文字元和标点，不增写设施、人数、光线或年龄限制。FAISS 召回 20 个候选，`qwen3-rerank` 精排后，视觉模型直接观看前三名视频。
3. 最终核验以原文为准，检查主体、地点、动作；目录标签只帮助定位，不构成画面证据。随机仅发生在核验合格且接近最佳得分的候选里，保留 seed 可复现。
4. 三个都不相符时排除已拒绝片段，细化同义查询并扩大召回至 60、180，分别重新精排和观看。最多三轮；仍不符则标为缺素材，不能拿卧室代替厕所。
5. 配音长于镜头时，先复用核验合格的候选，必要时重新召回、精排并观看补充镜头，写入场景 `shots`。用多个动态镜头覆盖整句，不靠长定格、黑屏或截短口播补时。`cuts.json` 记录实际裁切区间。
6. 用户允许的环境空镜替代记在 `visual_note` 和制作说明中，不在成片上叠加“相关画面示意”。原文未展示的具体动作不能伪称为实际拍到。
7. 成片从 `selected.path` 的原文件裁切，不使用索引代理画面。先检查每段色彩信息：普通 BT.709 素材保持原色，HDR（HLG/PQ）以线性光色调映射转换为 SDR，再统一编码为 BT.709 limited。不能只修改 HDR 标签冒充转换，也不能让整片继承第一个 HDR 镜头的标记。原文件只读；`cuts.json` 记录逐镜头色彩处理。

## 配音与交付

- 默认使用 IndexTTS2，按每个文案场景分别合成，测量实际音频时长，再按输出帧对齐并拼接。此为**场景级配音时间轴**，不声称得到词级 ASR 对齐。字幕整段出现与对应画面同帧切换。
- 用户只要无配音预览时跳过 `voice`；此时时间轴为阅读速度估算，必须标为 estimated。
- 原始情绪音频保留；上传使用它的前 15 秒副本。TTS task_id 会立即保存；等待超时后继续查询同一任务，不能自动重复下单。
- 配乐默认使用已实测的 `suno_music_open` 纯音乐，风格依据文案选择；用户指定其他服务时按其要求使用。音乐淡入淡出、压低音量并在人声出现时自动避让。只要无声预览时不生成音乐。
- 当前音量偏好：人声约 -19 LUFS、音乐约 -25 LUFS，轻度避让（ratio=2）。背景音乐应清楚可闻，不能在口播期间被压到几乎听不见，同时保留口播清晰度。
- 交付检查通过的 `video-music.mp4`（有声配乐）或 `preview.mp4`（无声）、`plan.json`、`captions.srt`/`estimated.srt`、`cuts.json`、`quality-report.json`。`video.mp4` 是混入音乐前的中间产物，不能冒充最终成片。
- 报告当前索引覆盖量，不把小样索引说成全盘完成。接口返回 200 还要检查向量非空、候选合法、视频文件可播放。

接口格式、上传兼容性、恢复方法见 [references/providers.md](references/providers.md)。

## 每次成片检查与修正

- 每次生成或修改后都执行 `cli.py render`，由 `gemini-3.7-flash` 观看**实际导出的视频与声音**，不是只看文案、选片或静音抽帧。审核代理连续覆盖全片，每段最多 40 秒，保留音轨；最后一段必须检查到口播结尾。
- 同时用 FFprobe 检查画面、音轨和配音时间轴时长，以及输出 color_space/transfer/primaries 均为 BT.709；禁止用 `-shortest` 把配音截断掩盖时长问题。检查黑屏、定格、缺音、字幕遮挡/错位、肤色异常、明显画面冲突、音乐听不见或盖过口播和人声歌词。
- `render` 对明确的画面问题自动换镜头并复查一次。未通过时读取 `quality-report.json` 的具体证据，继续针对性修正；文件生成成功不等于检查通过。报告绑定最终文件 SHA256，改动后旧报告失效。
- 优先补充或替换合格动态素材。用户已允许为解决时长问题进行同义增减文案；确实需要时保留原文及逐段修改记录，含义、数字、事实承诺保持不变，不能添加没依据的设施/服务或为了凑时长删掉关键信息。修改后重新 `voice` 测量实际时长，再 `render` 与 Gemini 复查。
- 总计最多三轮自动/代理修正；仍有错误时明确交付为待修订版本，列出具体缺素材或配音问题，不无限生成、无限计费，也不把失败报告改成通过。普通 warning 需人工核对实际画面/声音后说明。

## 全量任务与进度

- `index_all.py` 直接读取 Z 盘，只将代理视频、向量、描述与索引保存到本地。`catalog.sqlite3` 逐段提交，`.npy`/描述缓存用于中断恢复；失败片段有一轮自动重试。
- 看板从 SQLite 的 `index_progress` 读取真实提交数，2 秒刷新，显示视频/片段完成量、活跃文件、失败记录、速度和预计剩余时间。没有新心跳时明确提示，不能假装仍在正常处理。
- 最后生成 `vectors.faiss` 和 `verification.json`，核对所有预期片段、源文件变更及向量维度；只有全量通过才写入启用指针。匹配每次读取启用指针，无需再次配置目录。
- 若 `index.lock` 存在，先核对其 PID 是否仍是同一索引任务；禁止重复启动或删除活跃锁。确定进程已退出后才清理锁，并用相同命令续建。
- 旧 `cli.py index` 为 4 FPS 小样索引兼容入口，不用于本次 1 FPS 全量任务；不要将两种参数的向量写到同一目录。
