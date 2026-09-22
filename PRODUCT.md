# 海南康养视频工作台

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

用户制作海南康养、旅居视频。已确认第一版采用制作工作台：输入文案、设置配音、自动出片，而非聊天界面。

## Product Purpose

把中文文案交给既有 NAS 混剪服务，从真实素材匹配画面，生成配音、字幕、音乐，并展示检查通过的成片。

## Operating Context

原视频和 FAISS 库留在绿联 NAS；未来网页后台部署到新加坡 Ubuntu 服务器，经 Tailscale 调用 NAS。当前已验证服务器到 NAS 的健康检查，尚未部署智能体网页。本地开发通过 NAS 局域网接口验证。

## Capabilities and Constraints

- 复用已有 FastAPI NAS 任务 API，新增薄网页后台和任务记录，不更换检索或渲染算法。
- 固定主音色为用户提供的 9月21日.mp3；情绪参考保持独立。强度默认 0.8，范围 0.1–0.85，步长 0.05。
- 原素材裁切，保持既有色彩处理；Suno 纯音乐；Gemini 3.7 Flash 检查实际成片和声音。
- 制作任务保存在 NAS，关闭网页或电脑不停止任务。网页必须展示真实日志，不能伪造进度或通过结果。
- 第一版是单工作区；公网使用登录保护，NAS 与模型密钥不交给浏览器。不宣称有多租户、素材上传自动索引或独立原素材搜索 API。
- 新加坡部署入口和域名未提供，完成可运行版本与部署文件，并明确区分本地验证和远程上线。

## Brand Commitments

保留现有“海南康养 / 视频工作台”的暖白、绿色风格；用户选择直接开发可操作页面，再检查桌面和手机效果。

## Evidence on Hand

video-material-match/assets/control.html 为已有界面；video-material-match/references/nas-api.md 为接口协议；NAS部署验收.md 记录真实出片。现有 NAS 验收任务可读取，不需为界面演示重复调用付费模型。

## Product Principles

- 文案到成片的主操作始终清楚。
- 不确定的提交可使用同一标识核对，避免重复计费。
- 接口错误给出可执行的恢复方式。
- 成片、日志、检查报告有对应的真实任务。
