---
name: "海南康养视频工作台"
description: "沿用暖白与森林绿的中文视频制作工作界面"
colors:
  bg: "#f5f4ee"
  paper: "#fffefa"
  side: "#eceee5"
  ink: "#20362f"
  muted: "#536359"
  green: "#245e47"
  green-hover: "#174831"
  line: "#d9dfd4"
  soft: "#e6eee4"
  red: "#a33224"
  amber: "#835811"
typography:
  headline:
    fontFamily: '"Microsoft YaHei","PingFang SC",system-ui,sans-serif'
    fontSize: "28px"
    fontWeight: 650
    lineHeight: 1.45
    letterSpacing: "-.02em"
  title:
    fontFamily: '"Microsoft YaHei","PingFang SC",system-ui,sans-serif'
    fontSize: "15px"
    fontWeight: 600
  body:
    fontFamily: '"Microsoft YaHei","PingFang SC",system-ui,sans-serif'
    fontSize: "14px"
  editor:
    fontFamily: '"Microsoft YaHei","PingFang SC",system-ui,sans-serif'
    fontSize: "15px"
    lineHeight: 1.95
  small:
    fontFamily: '"Microsoft YaHei","PingFang SC",system-ui,sans-serif'
    fontSize: "12px"
    lineHeight: 1.7
rounded:
  status: "5px"
  utility: "6px"
  field: "7px"
  control: "8px"
  panel: "12px"
spacing:
  "8": "8px"
  "12": "12px"
  "16": "16px"
  "20": "20px"
  "24": "24px"
  "30": "30px"
components:
  button-primary:
    backgroundColor: "{colors.green}"
    textColor: "{colors.paper}"
    rounded: "{rounded.control}"
    padding: "11px 18px"
  button-primary-hover:
    backgroundColor: "{colors.green-hover}"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "11px 18px"
  button-text:
    backgroundColor: "transparent"
    textColor: "{colors.green}"
    padding: "6px 0"
  input-field:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "10px 12px"
  history-item:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "13px 11px"
  history-item-current:
    backgroundColor: "{colors.paper}"
  state-tag:
    backgroundColor: "{colors.soft}"
    textColor: "{colors.green}"
    rounded: "{rounded.status}"
    padding: "5px 8px"
  editor-sheet:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
  format-choice:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "8px 10px"
---

# Design System: 海南康养视频工作台

## Overview

**Creative North Star: "海南康养视频工作台"**

这是既有视频制作界面的延续：暖白工作面、森林绿操作提示、清晰的中文内容。它承接已有控制页的视觉语言，以可持续使用的工作台为定位，不另设品牌比喻或营销视觉。

内容输入、任务状态与真实成片共用一套克制的视觉层级。桌面允许设置与预览并排，窄屏保持编辑在前、预览在后的阅读顺序；密度来自任务信息和表单，而非装饰。

**Key Characteristics:**

- 暖白底与浅灰绿分区，森林绿集中标记可执行操作和任务状态。
- 中文系统字体、短标题与宽松文案行距，优先保证输入和阅读。
- 细边框、柔和圆角、内联线性 SVG 图标，工作面保持平坦。
- 状态同时使用文字和颜色；日志渐进展开，成片使用原生播放器。

本文件依据 `workbench/static/style.css`、`index.html` 和 `app.js` 的已实现界面记录；`PRODUCT.md` 的品牌承诺与 `.impeccable/workbench-brief.md` 提供已确认的延续范围。前置令牌描述复用值，不把每个局部样式升级为全局尺度。

## Colors

配色以偏暖纸面与浅灰绿结构区为主，深绿文字保持安静而清晰；前置令牌沿用 CSS 自定义属性名称。

### Primary

- **森林绿 — green：** 主按钮、链接、选中画幅、当前阶段和检查通过提示。
- **深森林绿 — green-hover：** 主按钮及文字操作的悬停反馈。
- **浅叶绿 — soft：** 次要按钮悬停与普通状态标签底色。

### Secondary

- **砖红 — red：** 错误、失败、中断、未提交成功及离线提示；属于功能状态色。
- **赭黄 — amber：** 提交中、等待核对等尚未确定的状态；属于功能状态色。

### Neutral

- **暖白 — bg：** 页面连续背景。
- **纸白 — paper：** 文案输入工作面、原生字段及当前任务；同时作为绿色按钮文字。
- **浅灰绿 — side：** 任务导航区域。
- **墨绿 — ink：** 主要文字及临时通知背景。
- **灰绿 — muted：** 辅助说明、时间、参数标注。
- **浅灰绿线 — line：** 分栏、分节与次要按钮边框。

### Named Rules

**The Action Green Rule.** 森林绿承担操作、选中和明确状态反馈，大片背景沿用中性纸面。

**The Status With Words Rule.** 状态必须有可读文字，绿色、砖红和赭黄不能单独传达任务结论。

## Typography

**Body Font:** Microsoft YaHei，依次回退至 PingFang SC、system-ui、sans-serif。标题与表单沿用相同字体；这是用户确认的中文工作界面选择，没有独立的展示字体体系。

**Character:** 字体层级紧凑，主要通过字号、字重和留白组织信息。长文案使用明显宽于辅助说明的行距；时间、字数和情绪数值使用等宽数字特性。

### Hierarchy

- **Headline：** 页面标题使用前置 headline 令牌；手机标题缩至（25px），登录页也有局部尺寸调整。
- **Title：** 普通分节标题使用 title 令牌；预览、进度等次级标题在（13–14px）范围内调整。
- **Body：** 根字号使用 body 令牌。普通段落行高为（1.8）；页面引导和部分设置值采用（13px）。
- **Editor：** 文案输入使用 editor 令牌，长段落保持可读。
- **Small：** 小号说明使用 small 令牌；历史时间、字数、范围端点和页脚有（11px）局部元数据样式。

辅助字号不作为新内容正文的默认大小。历史标题允许单行省略，并通过完整标题属性补充；错误和日志允许长文本换行。

### Named Rules

**The Reading First Rule.** 延续中文系统字体与文案行距；不把紧凑元数据字号扩展到主输入内容。

## Layout

桌面应用由固定宽度任务栏与弹性主区构成。常规任务栏为（224px），主区最大宽度为（1390px），主内容左右内边距为（42px）。工作区采用弹性编辑列与（300px）预览列，列间距为（36px）。这些是已实现工作台的布局参数，不是所有未来页面的固定模板。

实际响应式断点：

- 宽度至少（1500px）：预览列增至（330px），列间距为（44px），文案区域增高。
- 宽度至多（1180px）：任务栏收至（190px），主区左右内边距为（28px），预览列为（248px），列间距为（24px）。
- 宽度至多（990px）：侧栏转为顶部导航；制作记录折叠展开，展开后为两列任务记录；编辑与预览仍可并排。
- 宽度至多（760px）：主区左右内边距为（20px），编辑与预览转单列，预览移至设置之后。顶部新建按钮隐藏，制作记录面板内提供“新建视频 / 返回草稿”入口。

控件内部常见间隔为前置 spacing 的较小步骤，分节常用（24px、30px）。不是严格单一倍数网格：现有（9px、10px、22px、26px）等局部间距保留在组件中，不补造全局令牌。手机预览竖屏最大宽度为（330px），横屏可使用列宽；内容自然纵向滚动，无悬浮底部操作条。

## Elevation & Depth

工作面通过背景色、细边框与分隔线形成深度。编辑面板、导航项目和播放器在静止状态不使用投影；临时 toast 使用当前实现中的唯一浮层阴影（`0 8px 24px #20362f26`），该值仅记录在 sidecar，不扩展为多级阴影尺度。

### Named Rules

**The Flat Workspace Rule.** 表单、任务记录和预览依靠色面与边线分层，投影仅服务临时覆盖通知。

## Shapes

主工作面与媒体框采用 panel 圆角，按钮和任务项采用 control 圆角，原生字段与画幅选择采用 field 圆角。状态标签更紧凑，使用 status 圆角；图标按钮和日志区使用 utility 圆角。保留这些现有细微区别，不强行统一所有圆角。

边框以单像素为主。图标采用内联 SVG、无填充、圆端点和圆连接，常规尺寸为（20px），描边宽度为（1.65）；较小或较大的图标根据所在控件调整。画幅选项的小矩形以 CSS 细框表达比例，不使用文字字形替代图标。

## Components

### Buttons

操作明确、触感轻。

- 主按钮使用森林绿与纸白文字，前置令牌规定圆角和内边距，常规最小高度为（44px）。
- 悬停进入深森林绿；按下反馈使用现有更深色（#103a29），属于组件状态值。
- 次要按钮透明底配细边框，悬停进入浅叶绿；文字按钮以下划线表达可点击性。
- 常规按钮背景与文字过渡为（0.18s ease-out）；禁用按钮透明度为（0.5）并使用不可操作光标。
- 创建和下载主操作铺满所在列；创建按钮最小高度为（49px）。

### Chips

状态标签使用紧凑圆角与文字，不表现为可点击按钮。普通状态为浅叶绿底；失败、中断和未提交成功使用砖红文字与暖红底；等待核对、提交中使用赭黄文字与浅黄底。真实状态文字必须同步更新。

### Cards / Containers

纸白编辑面板包含标题区、可拉伸文本区和独立底部信息栏；外框负责定义工作边界，内区不重复套卡片。其他设置区通常直接落在页面背景上，通过留白和分隔线区分。媒体预览以比例框裁切外缘，视频本身用 contain 保留完整画面。

### Inputs / Fields

密码、选择框与画幅选项保留熟悉的原生交互。密码和选择框最小高度为（42px），边框使用现有字段色（#bfcbbf）；画幅选中同时改变边框、文字和背景。情绪滑块使用森林绿原生 accent-color。

聚焦时显示（2px）森林绿轮廓，常规外偏移（4px）；文案框轮廓向内偏移，画幅选项的可见代理元素使用（3px）外偏移。历史任务文案只读，参数控件禁用；表单错误在相关操作附近给出文字。全局尊重 prefers-reduced-motion，关闭过渡与动画。

### Navigation

任务导航以标题、时间与状态组成。悬停使用浅灰绿背景；当前记录使用纸白背景和细边框，并设置 aria-current。筛选按钮通过 aria-pressed 表达选择；制作记录展开按钮使用 aria-expanded。手机折叠记录中保留新建／返回草稿入口，选择任务后收起记录并显示其对应内容。

### Task And Delivery

这是界面中可复用的内容联动模式：选择任务时，文案、参数、阶段、日志和成片一起切换；已保存的任务保持只读，“沿用文案新建”进入可编辑草稿。

四段阶段条以文字和色条表示真实任务阶段，不显示虚构百分比。日志默认放入 details 渐进展开。中断状态明确说明页面暂不支持续作，新建会创建新任务。可交付状态显示原生播放器、下载成片、字幕及检查报告入口；预览状态与任务一致，不用装饰图片冒充成片。

## Do's and Don'ts

### Do:

- **Do** 延续暖白纸面、森林绿操作提示与中文系统字体。
- **Do** 用可读文字补充每一种连接、提交和制作状态。
- **Do** 为键盘操作保留可见焦点，并尊重减弱动画偏好。
- **Do** 在手机制作记录中保留新建或返回草稿入口。
- **Do** 让预览、日志与当前选择的真实任务对应。

### Don't:

- **Don't** 将绿色铺成与操作无关的大面积装饰。
- **Don't** 把元数据的小字号用于文案输入或主要说明。
- **Don't** 给普通工作面增加现有系统中不存在的多级投影。
- **Don't** 用虚构进度、占位影片或成功颜色代替真实任务结论。
