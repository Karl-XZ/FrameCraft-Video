# FrameCraft-Video

<div align="center">

**Multi-Agent Generative Science Animation Video Studio**  
*Transform topics, manuscripts, and media files into dynamic motion-graphics videos via openJiuwen, DeepSeek, and HyperFrames.*

[Bilibili Demo](https://www.bilibili.com/video/BV1Q6jC6QEPv/) • [Core Capabilities](#key-capabilities) • [Input Modes](#three-input-modes) • [Multi-Agent Architecture](#multi-agent-architecture) • [Quality Gates](#quality-gates--visual-verification) • [Local Setup](#local-development--deployment) • [Security & Privacy](#security-and-data-retention)

</div>

---

## Overview

**FrameCraft-Video** is an intelligent, multi-agent generative video platform that converts science topics, raw manuscripts, or multimedia recordings into motion-graphics explainer videos with synchronized voiceovers, subtitles, and programmatic SVG/GSAP animations. 

The backend orchestrates a collaborative hierarchy of **DeepSeek** language models through the **Huawei openJiuwen** multi-agent runtime, coupled with **Alibaba Cloud Model Studio (Qwen3 TTS / ASR)** for speech synthesis and recognition. Rendered using the official **HyperFrames** engine, final video frames are compiled directly inside the user's browser via **WebCodecs (H.264/AAC hardware acceleration)**. This ensures data sovereignty, rapid compilation, and zero cloud GPU video rendering overhead.

<div align="center">
  <img src="docs/assets/readme/workbench-chat-and-progress.png" alt="FrameCraft Workbench and Progress" width="850"/>
  <p><em>Figure 1: FrameCraft Studio Workspace with Real-Time Agent Progress & Chat</em></p>
</div>

<div align="center">
  <img src="docs/assets/readme/final-video-sample.png" alt="Final Rendered Video Preview" width="850"/>
  <p><em>Figure 2: Rendered Scientific Animation Output Sample</em></p>
</div>

---

## Key Capabilities

- 🤖 **Hierarchical Multi-Agent Orchestration**: Powered by Huawei openJiuwen TeamRuntime. Parallel specialist agents (`Narrative`, `Visual Metaphor`, `Timing`, `Art Director`, `Per-Scene Coders`, `Code Reviewers`, and `Quality Critics`) collaboratively plan, design, and code each scene.
- 📐 **Direct Code Synthesis (SVG + HTML + CSS + GSAP)**: Each scene is written from scratch with custom semantic animations, featuring clear entrance, continuous evolution, and exit transitions.
- 🎙️ **Flexible Audio Synthesis & Transcription**: Integrates Qwen3 TTS for natural narration and Qwen ASR for transcribing uploaded media while preserving original audio fidelity.
- ⚡ **Browser-Side WebCodecs Rendering**: Compiles HyperFrames projects into high-definition H.264/AAC MP4 files inside the client browser, maintaining zero server-side video queue bottlenecks.
- 🛡️ **Dual-Stage Quality Gates**: Enforces programmatic duration checks on the client alongside multi-frame vision inspections (`entrance`, `midpoint`, `exit`) via multimodal models to verify visual clarity and aesthetic standards.
- 💬 **Post-Release Conversational Refinement**: The conversational assistant inspects project code and review logs to execute full regenerations, single-scene rebuilds, or localized fine-tuning based on user feedback.
- 🔒 **Zero Server Video Storage**: Project manifests and code reside securely on the server; MP4 video outputs remain strictly within the user's browser memory until downloaded.

---

## Three Input Modes

| Mode | User Input | Content Processing Policy | Audio Source |
| :--- | :--- | :--- | :--- |
| **Topic Mode** | Subject title, target audience, duration | DeepSeek generates educational narrative, scene division, and visual themes without external web fabrication. | Alibaba Cloud Qwen3 TTS |
| **Script Mode** | Full presentation manuscript | Preserves exact text sequence and structure; segments sentences according to punctuation. | Alibaba Cloud Qwen3 TTS |
| **Media Mode** | Audio or video file | Alibaba Cloud ASR transcribes spoken tracks; animation strictly synchronizes with the original timeline. | Original user audio track |

---

## Multi-Agent Architecture

```text
React Web Studio
  │
  ├──► FastAPI REST / WebSocket Gateway (Projects, Assets, Tasks, Chat)
  │
  ├──► Input Ingestion: Topic (DeepSeek Outline) / Script (Direct) / Media (Qwen ASR)
  │
  ├──► Speech Synthesis: Qwen3 High-Fidelity TTS
  │
  ├──► openJiuwen TeamRuntime (Collaborative Agent Swarm)
  │      ├── narrative: Scientific storytelling, conceptual pacing, and data hierarchy
  │      ├── visual: Symbolic visual metaphors and semantic motion choreography
  │      ├── timing: Rhythm synchronization, subtitle placement, and screen density
  │      ├── art_director: DeepSeek Pro defining global color palette and motif rules
  │      ├── scene_designer_N: Dedicated parallel agents coding SVG / HTML / CSS / GSAP
  │      ├── scene_code_reviewer_N: Per-scene validation of syntax, safety, and originality
  │      ├── code_director: Global typography consistency and cross-scene visual continuity
  │      └── quality_critic: Rigorous scientific accuracy and graphical aesthetics audit
  │
  ├──► HyperFrames Project Compilation (HTML, timeline.json, subtitles.srt)
  │
  ├──► Browser-Side Execution:
  │      ├── HyperFrames official runtime deterministic renderSeek frame-by-frame loop
  │      ├── WebCodecs hardware-accelerated H.264 / AAC MP4 encoding
  │      └── Contact sheet generation for remote visual audit
  │
  └──► Conversational Refinement Loop (Chat AI):
         ├── Full Regeneration: Reprompts pipeline with summarized user feedback
         ├── Single-Scene Repair: Reruns generation for a specific scene while freezing others
         └── Fine-Tuning: Directly modifies existing project code followed by fast local re-render
```

The execution trace is persisted to `outputs/<project_id>/analysis/agent_trace.json`, detailing team topology, model choices, latency metrics, and structured outputs for every run.

---

## Quality Gates & Visual Verification

- **Subtitle Alignment**: Subtitles must cover the complete narration track, positioned centrally in the lower safe area with smooth transitions.
- **Accurate Data Presentation**: Quantitative data points must link to verified facts; qualitative topics use symbolic diagrams.
- **Visual Structure Diversity**: System relationships, timelines, comparative data, and mechanisms utilize distinct visual layouts.
- **Deterministic Seek Verification**: All animations must render deterministically via HyperFrames `renderSeek`. Any build failing programmatic checks triggers automated project repair up to 3 times.
- **Traceable Agent Identity**: Every generated scene retains its source code and originating agent metadata to ensure complete auditability.

---

## Local Development & Deployment

### Prerequisites

- Python 3.10+
- Node.js 18+ (Node.js 20+ recommended)
- Modern browser with WebCodecs support (Chrome or Edge recommended)

### Environment Configuration

Create a `.env` file in the backend root:

```bash
# DeepSeek API credentials
DEEPSEEK_API_KEY=your_deepseek_api_key_here

# Alibaba Cloud Model Studio credentials (Qwen TTS & ASR)
DASHSCOPE_API_KEY=your_dashscope_api_key_here

# Server host & port configurations
FRAMECRAFT_BACKEND_HOST=0.0.0.0
FRAMECRAFT_BACKEND_PORT=8022
FRAMECRAFT_RETENTION_ENABLED=1
FRAMECRAFT_RETENTION_HOURS=24
FRAMECRAFT_RENDER_TARGET=local
```

### Start Backend and Frontend

```bash
# Start backend service
./scripts/start-backend.sh

# Start frontend application
./scripts/start-frontend.sh
```

Open `http://localhost:3000` to launch the FrameCraft Studio.

### Automated Testing

```bash
# Run backend test suite
backend/venv-openjiuwen/bin/python -m unittest discover -s backend/tests -v

# Run frontend lint and build verification
cd framecraft-agent && npm run lint && npm run build
```

---

## Security and Data Retention

1. **Sandboxed Code Execution**: The browser unpackages project bundles into isolated worker contexts without executing untrusted scripts on the backend server.
2. **Project Privacy**: Public endpoints disable project enumeration (`GET /api/projects`) by default. Users access project records exclusively through their private tokenized URLs.
3. **Zero Video Ingestion**: Rendered MP4 files exist solely within browser memory and local user storage; video files are never transmitted to or hosted on the central server.
4. **Automated Resource Expiry**: Automated cleanup scripts purge intermediate artifacts and temporary project directories after 24 hours of inactivity.
