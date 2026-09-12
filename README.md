# FrameCraft-CN 一键科普视频版

FrameCraft-CN 把一个科普主题、一篇中文文案或一段音视频，转换为带旁白、简体中文字幕和科学动态图解的 HyperFrames 视频工程。服务端使用华为 openJiuwen 编排 DeepSeek 多 Agent，阿里云百炼提供 TTS 与 ASR，最终 MP4 默认在用户电脑真实渲染并下载。

项目不处理人物口播，不生成剪映草稿，也不提供 FFmpeg 静态拼接兜底。HyperFrames、媒体检查或视觉验收没有通过时，系统会保留真实失败原因，不注册伪成功版本。

## 功能

- 输入主题：DeepSeek 自动生成科普讲稿、章节、视觉主张和来源台账，再调用阿里云 TTS。
- 输入文案：严格按照用户原文生成阿里云 TTS、字幕和视频，不改写正文。
- 上传媒体：阿里云 ASR 转写音频或视频原音轨；最终成片完整使用原音频，不重新配音。
- 多 Agent 设计与编码：内容、视觉、时序专家并行调研，总导演建立艺术圣经，每幕由独立 Agent 直接编写 SVG、HTML、CSS 与 GSAP，独立代码审查 Agent 验收后再整合。
- 原创语义动画：逐幕源码必须针对当前内容建立独立构图，并包含入场、持续演化和退场；旧成片模板、固定母题换字和近似重复代码会被拒绝。
- 固定来源标注：引用短注始终位于画布左下角安全位，不跟随场景构图漂移。
- 真实 HyperFrames：浏览器调用 HyperFrames 官方 runtime 确定性逐帧渲染，并通过 WebCodecs 输出 H.264/AAC MP4。
- 双重验收：浏览器检查编码能力、时长与音视频轨，DeepSeek 视觉模型逐幕检查入场、中段、退场三帧。
- 项目隔离：每个项目拥有独立素材、聊天、Agent 跟踪、工程和版本记录。
- 项目私密入口：公网端不展示全局项目列表，默认也不开放项目枚举 API；用户只通过自己创建后的项目链接进入。
- 初版后对话改片：对话 AI 读取当前工程代码和验收记录，判断用户需要完整重新生成还是局部微调，并由用户点击按钮后执行。
- 工程留云端：服务器保存可复渲染工程，MP4 只在用户电脑生成和下载。
- 自动清理：生产环境默认清理超过 24 小时且没有活动任务的项目资源。

## 演示

[查看 Bilibili 演示视频](https://www.bilibili.com/video/BV1Q6jC6QEPv/)

![FrameCraft-CN 工作台预览](docs/assets/readme/workbench-chat-and-progress.png)

![FrameCraft-CN 成片示意](docs/assets/readme/final-video-sample.png)

## 三种输入模式

| 模式 | 用户输入 | 内容边界 | 声音来源 |
| --- | --- | --- | --- |
| 主题 | 主题、受众、额外要求 | DeepSeek 生成讲稿并核验来源链接 | 阿里云 Qwen3 TTS |
| 文案 | 完整演讲稿 | 保持原文字序和内容，只按标点分段 | 阿里云 Qwen3 TTS |
| 媒体 | 音频或含音轨的视频 | 阿里云 ASR 转写，视觉严格跟随原音频 | 用户原音频 |

完整规范见 [一键科普视频生成工作流](docs/SCIENCE_VIDEO_WORKFLOW.md)。

## 架构

```text
React 网页工作台
  -> FastAPI 项目、素材、任务和聊天 API
  -> 主题：DeepSeek 讲稿与来源 / 文案：原文 / 媒体：阿里云 ASR
  -> 文本输入使用阿里云 Qwen3 TTS
  -> openJiuwen TeamRuntime
       -> narrative：科学叙事与信息层级
       -> visual：视觉隐喻与语义运动
       -> timing：节奏、字幕和同屏密度
       -> art_director：DeepSeek Pro 制定全片艺术圣经和母题分配
       -> scene_designer_N：每幕一个 DeepSeek Agent 并行设计并编写 SVG/HTML/CSS/GSAP
       -> scene_code_reviewer_N：逐幕检查语义、原创性、安全边界和可执行性
       -> code_director：DeepSeek Pro 统一文字层级与连续性，不覆盖逐幕源码
       -> quality_critic：检查科学表达、场景差异和动态图形质量，不通过则触发一次完整修订
  -> 原样整合逐幕 Agent 源码，生成 HyperFrames HTML、时间线、字幕和来源台账
  -> 浏览器下载 HyperFrames 工程包并安全解包到内存
  -> HyperFrames 官方 runtime renderSeek 逐帧渲染
  -> 浏览器 WebCodecs 编码 H.264/AAC + 临时联系表视觉验收
  -> MP4 在当前电脑预览和下载，服务器只保存工程
  -> 初版后：对话 AI 读取最新工程，给出重新生成或微调按钮
       -> 重新生成：整理用户需求给提示词 AI，完整生成新工程
       -> 微调：对话 AI 修改现有 HyperFrames 工程文件后重新本地渲染
```

`outputs/<project_id>/analysis/agent_trace.json` 保存 openJiuwen 团队拓扑、模型、耗时和结构化结果，用于确认每次任务真实经过多 Agent。

## 模型分工

| 职责 | 默认模型或服务 |
| --- | --- |
| 内容、视觉、时序与逐幕设计 Agent | `deepseek-v4-flash` |
| 科普写稿、总导演、整合导演 | `deepseek-v4-pro` |
| 成片视觉验收 | `deepseek-v4-flash-vision-exp` |
| 文本转语音 | 阿里云百炼 `qwen3-tts-flash` |
| 语音转文字 | 阿里云百炼 `qwen3-asr-flash` |

## 环境要求

- Python 3.11
- Node.js 22 或更高版本
- npm
- `ffmpeg` 与 `ffprobe`
- Chromium 或 Chrome
- DeepSeek API Key
- 阿里云百炼 DashScope API Key
- HyperFrames CLI，当前锁定 `0.7.41`

Ubuntu 示例：

```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv ffmpeg curl fonts-noto-cjk
```

## 安装

```bash
git clone https://github.com/Karl-XZ/FrameCraft-CN.git
cd FrameCraft-CN
./scripts/setup-deps.sh
./scripts/verify-env.sh
```

## 配置

```bash
export DEEPSEEK_API_KEY='your-deepseek-key'
export DEEPSEEK_BASE_URL='https://api.deepseek.com'

export DASHSCOPE_API_KEY='your-dashscope-key'
export DASHSCOPE_BASE_URL='https://dashscope.aliyuncs.com/api/v1'
export DASHSCOPE_COMPATIBLE_BASE_URL='https://dashscope.aliyuncs.com/compatible-mode/v1'
export DASHSCOPE_TTS_MODEL='qwen3-tts-flash'
export DASHSCOPE_TTS_VOICE='Cherry'
export DASHSCOPE_ASR_MODEL='qwen3-asr-flash'
```

不要把真实密钥写入 Git、README、前端源码、浏览器构建产物或日志。后端设置接口不会向前端回显已保存的密钥。

可选运行参数：

```bash
export FRAMECRAFT_BACKEND_HOST=0.0.0.0
export FRAMECRAFT_BACKEND_PORT=8022
export FRAMECRAFT_RETENTION_ENABLED=1
export FRAMECRAFT_RETENTION_HOURS=24
export FRAMECRAFT_RENDER_TARGET=local
```

## 启动

```bash
./scripts/start-backend.sh
./scripts/start-frontend.sh
```

访问者无需下载或启动任何辅助程序。公网工作台必须使用 HTTPS，并建议使用最新版 Chrome 或 Edge，以获得 WebCodecs 的 H.264/AAC 硬件编码支持。工程 ZIP、逐帧画布和 MP4 全部只存在于当前浏览器内存；页面关闭后，如未下载成片，本地临时结果会随页面释放。

## 网页流程

1. 新建项目，选择主题、文案或媒体模式。
2. 设置画幅、目标时长和科学视觉风格；媒体模式进入工作台后上传文件。
3. 点击“开始生成科普方案”，等待内容准备和 openJiuwen 多 Agent 分析。
4. 查看方案并确认生成。
5. 网页下载工程，在当前浏览器中调用 HyperFrames 官方 runtime 逐帧渲染并编码 MP4。
6. 浏览器检查音视频轨与完整时长，并按每幕入场、中段、退场生成临时联系表。
7. 云端视觉 Agent 验收后删除联系表；通过时 MP4 直接进入当前浏览器预览和下载。
8. 初版生成完成前，项目聊天只提示先完成初版；初版完成后，项目聊天由对话 AI 读取当前工程代码和验收记录，判断用户诉求。
9. 需要整体重做时显示“重新生成”按钮，点击后把对话 AI 整理的需求交给提示词 AI 完整生成新版本。
10. 只需局部修改时显示“微调”按钮，点击后由对话 AI 修改现有 HyperFrames 工程文件，再重新本地渲染和验收。

## 质量门槛

- 文案模式字幕必须覆盖原文，媒体模式必须保留原音频完整时长。
- 字幕使用简体中文，固定居中放在底部安全区，并带渐入渐出。
- 观众画面禁止出现幕后制作文案。
- 精确数据必须可追溯；没有数字时只用定性图解，不伪造刻度。
- 机制、尺度、对比、时间线和系统关系使用不同主视觉结构。
- 关键节点逐个出现，并具有表达含义的持续运动。
- 主视觉充分利用画幅，避免拥挤、遮挡与无意义空白。
- HyperFrames 必须通过官方 runtime 的确定性 `renderSeek` 逐帧运行；视觉评分低于 82 时不登记通过版本。
- 验收失败后保留本机可播放成片，在 Agent 对话中展示分数和问题；系统不会自动重画，只有用户明确说“重试”或点击“重试”按钮才把验收意见反馈给提示词 AI 并生成新版本。
- 如果浏览器根本没有生成可播放 MP4，系统会把错误自动反馈给提示词 AI 修复工程，最多 3 次；超过上限后回到聊天说明问题。
- 初版后对话改片由对话 AI 分流：完整重新生成交给提示词 AI，局部微调由对话 AI 修改现有工程文件。
- 每幕必须保存独立源代码与生成、审查 Agent 身份；缺少源码或命中旧模板标记时立即停止。

不可退让的实现边界见 [多 Agent 科普视频生成需求](docs/MULTI_AGENT_ANIMATION_REQUIREMENTS.md)。

## 测试

```bash
backend/venv-openjiuwen/bin/python -m unittest discover -s backend/tests -v
cd framecraft-agent && npm run lint && npm run build
```

部署到 `/FrameCraft/` 子路径时使用：

```bash
cd framecraft-agent
FRAMECRAFT_PUBLIC_BASE=/FrameCraft/ npm run build
```

真实网页端主题模式：

```bash
backend/venv-openjiuwen/bin/python scripts/run_science_ui_flow.py \
  --mode topic \
  --input '为什么天空通常呈现蓝色，日落时偏红？' \
  --requirements '面向成年人，约一分钟，避免公式' \
  --studio 'https://your-framecraft.example.com' \
  --output benchmark-results/science-topic.mp4
```

`--mode script` 时 `--input` 传完整文案；`--mode media` 时传音频或视频绝对路径。网页端验收必须使用真实 API、真实 HyperFrames runtime 与浏览器 WebCodecs，不提供模拟成片。

## 产物

```text
outputs/<project_id>/
  analysis/analysis.json, edit_plan.json, creative_plan.json, agent_trace.json
  input/scene_seed.json, source_bundle.json, transcript.txt, SOURCE_LEDGER.md
  <version_id>/subtitles.srt, timeline.json, agent_visual_review.json
  <version_id>/local_render_manifest.json, hyperframes/, hyperframes_project.zip
```

## 安全与保留

- 浏览器解包器规范化 ZIP 路径，不将工程脚本交给服务器执行；渲染 iframe 与页面业务状态隔离。
- 默认关闭 `GET /api/projects` 项目枚举，避免不同用户互相看到项目名称、状态或创建记录；本机维护时可临时设置 `FRAMECRAFT_ALLOW_PROJECT_LIST=1`。
- 服务端忽略云端渲染请求；MP4 不上传服务器，历史预览接口返回 `410`。
- 服务器只接收临时联系表用于验收，接口结束后立即删除图片，只保存验收 JSON。
- MP4 使用浏览器对象 URL 预览，仅在当前标签页生命周期内保留；用户主动下载后由用户设备自行管理。
- 后端可执行 `FRAMECRAFT_RETENTION_HOURS=24 ./scripts/cleanup-expired.sh` 清理过期项目。
- 来源 URL 只允许公开 HTTPS 地址，并拒绝内网目标和越界重定向。

## 参考

- [openJiuwen Agent Core](https://github.com/openJiuwen-ai/agent-core)
- [openJiuwen 文档](https://docs.openjiuwen.com/)
- [DeepSeek API 文档](https://api-docs.deepseek.com/)
- [阿里云百炼 Qwen TTS API](https://help.aliyun.com/zh/model-studio/qwen-tts-api)
- [阿里云百炼 Qwen ASR API](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference)
- [HyperFrames](https://github.com/nateherk/hyperframes)
