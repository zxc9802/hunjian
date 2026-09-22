# 工作台音色试听

将与 NAS 生成音色一致的参考文件放在本目录，命名为 `speaker-reference.mp3`。这是可选的网页试听文件；缺少时工作台会禁用试听，不影响现有 NAS 使用它自己的参考音频生成。

私人音频已被 `.gitignore` 排除。新部署从 GitHub 克隆后，可单独放入自己的文件再构建镜像，或通过登录后的 `PUT /api/reference/voice` 上传同一份 MP3（详见 [Zeabur 部署说明](../ZEABUR.md#音色试听)）。上传文件保存在 `WORKBENCH_DATA/speaker-reference.mp3`，优先用于试听，不会替换 NAS 生成音色。
