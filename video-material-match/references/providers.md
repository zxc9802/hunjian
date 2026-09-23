# 模型与恢复

## 已实测接口

| 用途 | 地址 | 请求和返回 |
|---|---|---|
| 多模态 embedding | `https://api.openlux.ai/v1beta/models/gemini-embedding-2-preview:generateContent` | **content.parts**；视频使用 inline_data.mime_type=video/mp4 和 base64 data；读取 embedding.values，实测 3072 维 |
| 场景切片、描述、视频核验 | `https://api.openlux.ai/v1beta/models/gemini-3.7-flash:generateContent` | contents 数组；视频 inlineData；读取 candidates[].content.parts[].text，兼容代码围栏 |
| 文本精排 | `https://api.302.ai/v1/reranks` | model=qwen3-rerank，query、documents、top_n、instruct；results[].index/relevance_score |
| 配音提交 | `https://api.302.ai/302/index_tts2/task` | POST text、speaker_audio_url、emotion_audio_url、emotion_alpha；返回 task_id |
| 配音查询 | 同上 | GET ?task_id=...；state=SUCCESS 后读取 audio_url |

背景音乐仅使用用户提供的音频，不调用音乐生成接口。工作台上传、列曲和试听使用私有腾讯 COS 音乐库；NAS 按所选曲目下载后混音。未选音乐时保留口播，不添加背景音乐。

Authorization 均采用 Bearer，密钥只来自环境变量。embedding 与视觉模型虽都以 generateContent 结尾，**请求体并不相同**。本机实测 embedding 使用 contents 时文字可用，但视频报“content field must have at least one part”。不要退回只做文字 embedding。

Google 原生 [embedding 文档](https://ai.google.dev/gemini-api/docs/embeddings) 使用 `:embedContent`；该形式在 OpenLux 也已实测可用，默认仍保留用户给定的 URL。视频代理为 4 fps、宽 512 像素，8 秒片段；快动作/细小物体可能需要提高采样率，改变代理设置时用新索引目录。

## 情绪参考上传

主音色采用用户提供的 `9月21日.mp3`，保存在 `assets/speaker-reference.mp3`；它与下面的情绪参考分别上传，不能把两个 URL 混用。主音色原文件约 24 秒，按服务参数说明生成前 15 秒、22050Hz 单声道副本，原文件完整保留。上传缓存使用 `speaker-<hash>.json`，不会覆盖 `emotion-<hash>.json`。CLI 默认使用这份本地音色，也支持 `--speaker-file` 或 `--speaker-url` 覆盖。

用户原始文件位于 `C:\Users\78575\Downloads\indextts-emotion-reference.wav`，技能 assets 中是相同副本。原长 20 秒。按 [IndexTTS2 参数说明](https://nuxt.302.ai/product/detail/302ai-index-tts-2)，超过 15 秒的参考会被截取；本工具明确生成前 15 秒、22050Hz 单声道 WAV 后上传，原文件不改动。

1. 首选 [302 文件上传 API](https://doc-en.302.ai/232502112e0)：POST `https://api.302.ai/upload-file`，Bearer key，multipart 字段 `file`，返回 data 字符串 URL。
2. 该入口实测 503“当前无可用模型”。5xx 时使用 302 官方开源应用的上传入口 `https://dash-api.302.ai/gpt/api/upload/gpt/image`，multipart `file` + `need_compress=false`，无 Authorization；返回 data.url。该入口已成功上传本用户参考音频。
3. 来源：[302_tts/.env.example](https://github.com/302ai/302_tts/blob/main/.env.example)、[use-file-upload.ts](https://github.com/302ai/302_tts/blob/main/hooks/use-file-upload.ts)。只发到这些 302 官方入口，不擅自上传到其他网盘。

上传 URL 缓存 24 小时，以原音频 SHA256 区分；如 URL 失效，仅移除对应 voice-cache/emotion-*.json 记录后重传，不删除原音频。

## 恢复与限制

- index 每个成功片段立即写入 SQLite；中断重跑同样命令会跳过已完成片段。SQLite 保存元数据与向量，FAISS 保存检索索引。不同模型、切片参数不能混到同一 catalog。
- source 路径、文件大小与 mtime 一同校验，已删除/改变素材不参与召回；同大小同 mtime 的人工替换无法识别，应主动重建目录。
- match 每完成一个场景写入 plan.json。错误退出后重新执行 match，或在明确检查既有结果后复用；不将未完成 plan 当成可渲染结果。
- TTS 已收到 task_id 后会保存本地记录，仍在处理或下载未完成时重跑 voice 会接着查询原任务。明确返回 FAILURE/FAILED/ERROR/REVOKED 的段落在续作时重新提交一次，旧 task_id 与错误保留在 attempts 中；本次再失败就停止，不连续重复下单。提交前先保存状态；提交超时且没有 task_id 时先检查服务商任务，避免重复计费。每段成功后保存配音时间轴；同一主音色重新上传换链接时保留原缓存标识，已成功段落继续复用。
- 原视频超出 8 秒用连续窗口切片，不是真正的镜头边界检测。跨镜头区间由最终视觉核验筛选；持续很短的目标可能未被 4 fps 代理捕获。
- 无用户口播、ASR 或强制对齐，不承诺词级时间精度；当前是逐段 TTS 的实测场景边界。
