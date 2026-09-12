from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import json5
from . import store
from .ingest import PreparedSource, ensure_version_subtitles, prepare_source_bundle
from .deepseek_api import create_client, deepseek_available, deepseek_settings, parse_json_object
from .jiuwen_team import run_visual_review
from .two_stage_core import build_two_stage_analysis, materialize_two_stage_version

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "needs_input"}
HYPERFRAMES_ROOT = store.ROOT.parent / "hyperframes"
SCIENCE_WORKFLOW_DOC = store.ROOT / "docs" / "SCIENCE_VIDEO_WORKFLOW.md"
PROMPTS_DIR = store.ROOT / "backend" / "app" / "prompts"
FORBIDDEN_CMD_SNIPPETS = [
    "sudo ",
    "ssh ",
    "scp ",
    "rsync ",
    "curl ",
    "wget ",
    "git push",
    "git clone",
    "rm -rf /",
    "shutdown",
    "reboot",
    "diskutil",
    "launchctl",
    "osascript",
]
RENDER_QUALITY_SET = {"draft", "standard", "high"}
LOCAL_HYPERFRAMES_CLI = store.ROOT / "node_modules" / ".bin" / "hyperframes"


class ProjectBusyError(RuntimeError):
    pass


class SingleAgentRunner:
    def __init__(self) -> None:
        self._threads: dict[str, threading.Thread] = {}

    def start(self, project_id: str, job_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not deepseek_available():
            raise RuntimeError("DeepSeek API Key 未配置。请在模型设置中填写，或设置 DEEPSEEK_API_KEY。")
        job_id = store.new_id("job")
        now = store.now_iso()

        def op(data):
            active = [
                j
                for j in data["jobs"].values()
                if j.get("project_id") == project_id and j.get("status") not in TERMINAL_STATUSES
            ]
            if active:
                raise ProjectBusyError(f"该项目已有运行中的 agent 任务：{active[0]['id']}")
            project = data["projects"][project_id]
            project["status"] = (
                "analyzing" if job_type == "analyze" else "chatting" if job_type == "chat" else "rendering"
            )
            project["updated_at"] = now
            data["jobs"][job_id] = {
                "id": job_id,
                "project_id": project_id,
                "type": job_type,
                "status": "pending",
                "progress": 0.0,
                "current_step": "",
                "error_message": None,
                "created_at": now,
                "started_at": None,
                "completed_at": None,
                "payload": payload or {},
                "logs": [],
                "warnings": [],
                "completed_steps": [],
                "plan_substep": None,
                "plan_progress": 0,
            }
            return data["jobs"][job_id]

        job = store.mutate(op)
        thread = threading.Thread(target=self._run, args=(job_id,), daemon=True)
        self._threads[job_id] = thread
        thread.start()
        return store.public_job(job)

    def _run(self, job_id: str) -> None:
        self._mark_running(job_id)
        job = store.snapshot()["jobs"][job_id]
        project_id = job["project_id"]
        workspace = store.RUNTIME / "jobs" / job_id
        workspace.mkdir(parents=True, exist_ok=True)
        project = store.snapshot()["projects"].get(project_id) or {}
        input_mode = str(project.get("input_mode") or "media")
        if input_mode == "topic":
            self._set_step(job_id, 5, "DeepSeek 正在生成科普讲稿与章节")
        elif input_mode == "script":
            self._set_step(job_id, 5, "阿里云正在按原文生成科普旁白")
        else:
            self._set_step(job_id, 5, "阿里云正在转写上传媒体")
        try:
            prepared = prepare_source_bundle(project_id)
        except Exception as exc:
            self._needs_input(job_id, str(exc))
            return

        try:
            self._set_step(job_id, 18, "输入源准备完成，开始调用 Agent")
            self._append_log(job_id, f"已准备输入源：{prepared.mode}", chat=True)
            if job["type"] == "analyze":
                self._run_managed_analysis(job_id, prepared)
            elif job["type"] in {"generate", "apply_patch"}:
                self._run_managed_render(job_id, prepared)
            elif job["type"] == "fine_tune":
                self._run_dialogue_fine_tune(job_id, prepared)
            else:
                self._run_agent_loop(job_id, prepared)
            if not self._validate_outputs(job_id):
                return
            self._complete(job_id)
        except Exception as exc:
            self._fail(job_id, str(exc))

    def _run_agent_loop(self, job_id: str, prepared: PreparedSource) -> None:
        snapshot = store.snapshot()
        job = snapshot["jobs"][job_id]
        project_id = job["project_id"]
        project = snapshot["projects"].get(project_id) or {}
        cfg = deepseek_settings()
        api_key = cfg["api_key"]
        base_url = cfg["base_url"]
        model = cfg["text_model"]
        client = create_client()
        version_count_before = len(
            [v for v in snapshot["versions"].values() if v["project_id"] == project_id]
        )

        system_prompt = self._system_prompt(project, prepared)
        user_prompt = self._user_prompt(job, project, prepared)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        allow_render_tools = not (job["type"] == "chat" and not bool(job.get("payload", {}).get("apply", True)))
        tools = self._tool_definitions(allow_render_tools=allow_render_tools)

        for _step in range(48):
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.15,
            )
            msg = resp.choices[0].message
            message_payload: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content or "",
            }
            if msg.tool_calls:
                message_payload["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": _normalize_tool_arguments(tc.function.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(message_payload)

            if msg.content:
                self._append_log(job_id, _trim(msg.content, 1800))

            if not msg.tool_calls:
                break

            for call in msg.tool_calls:
                result = self._execute_tool_call(job_id, prepared, call.function.name, call.function.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                if result.get("terminal"):
                    return
        else:
            raise RuntimeError("Agent 达到最大工具调用轮数，未能收敛。")

        post = store.snapshot()
        version_count_after = len([v for v in post["versions"].values() if v["project_id"] == project_id])
        if job["type"] in {"generate", "apply_patch"} and version_count_after <= version_count_before:
            raise RuntimeError("Agent 对话结束了，但没有产出新的已注册版本。")

    def propose_chat_action(self, project_id: str, message: str) -> dict[str, Any]:
        snapshot = store.snapshot()
        project = snapshot["projects"].get(project_id)
        if not project:
            raise RuntimeError("项目不存在。")
        version = self._latest_interactive_version(project_id, snapshot)
        if not version:
            content = "请先完成初版生成并在当前浏览器生成一次 MP4，之后我就能基于成片和工程文件继续和你讨论修改。"
            return self._append_chat_action(project_id, content, "needs_initial_generation", None, "", {})

        context = self._dialogue_context(project_id, project, version, snapshot)
        payload = {"user_message": message, "context": context}
        response = create_client().chat.completions.create(
            model=deepseek_settings()["text_model"],
            messages=[
                {"role": "system", "content": (PROMPTS_DIR / "dialogue_ai_system.md").read_text(encoding="utf-8")},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.12,
            max_tokens=1800,
            extra_body={"thinking": {"type": "disabled"}},
        )
        decision = parse_json_object(response.choices[0].message.content)
        intent = str(decision.get("intent") or "answer").strip()
        if intent not in {"answer", "regenerate", "fine_tune"}:
            intent = "answer"
        reply = str(decision.get("reply") or "").strip() or "我已经读完当前工程上下文，可以继续帮你判断修改方式。"
        action = "regenerate_video" if intent == "regenerate" else "fine_tune_video" if intent == "fine_tune" else None
        payload = {
            "intent": intent,
            "user_message": message,
            "edit_summary": str(decision.get("edit_summary") or "").strip(),
            "target_files_hint": decision.get("target_files_hint") or [],
            "risk_note": str(decision.get("risk_note") or "").strip(),
            "base_version_id": version["id"],
        }
        return self._append_chat_action(project_id, reply, "proposed" if action else "chat", action, version["id"], payload)

    def _tool_definitions(self, allow_render_tools: bool = True) -> list[dict[str, Any]]:
        base_tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_state",
                    "description": "读取当前项目状态、素材、版本、聊天记录和关键路径。",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "列出目录下的文件。path 可为绝对路径，或相对当前项目输出目录。",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取文本文件内容。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "max_chars": {"type": "integer", "minimum": 200, "maximum": 50000},
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写入文本文件到当前项目目录或 job 工作目录。",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "make_dir",
                    "description": "创建目录。",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "在允许目录中执行本地命令，用于 HyperFrames、Node、ffmpeg、python 等。禁止联网或系统级危险命令。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "cwd": {"type": "string"},
                            "timeout_sec": {"type": "integer", "minimum": 5, "maximum": 7200},
                        },
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "report_progress",
                    "description": "更新当前任务进度和当前步骤。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "progress": {"type": "number", "minimum": 0, "maximum": 100},
                            "step": {"type": "string"},
                            "status": {"type": "string"},
                        },
                        "required": ["progress", "step"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_analysis",
                    "description": "写 analysis/analysis.json。",
                    "parameters": {
                        "type": "object",
                        "properties": {"payload": {"type": "object"}},
                        "required": ["payload"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_edit_plan",
                    "description": "写 analysis/edit_plan.json。",
                    "parameters": {
                        "type": "object",
                        "properties": {"payload": {"type": "object"}},
                        "required": ["payload"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_version_dir",
                    "description": "创建一个新版本目录，后续渲染产物都放进去。",
                    "parameters": {
                        "type": "object",
                        "properties": {"version_id": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "bootstrap_hyperframes_project",
                    "description": "在版本目录里离线创建一个最小可渲染的 HyperFrames 子工程，并同步版本素材到 hyperframes/assets。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "version_dir": {"type": "string"},
                            "width": {"type": "integer", "minimum": 360, "maximum": 3840},
                            "height": {"type": "integer", "minimum": 360, "maximum": 3840},
                            "duration_s": {"type": "number", "minimum": 1, "maximum": 1800},
                        },
                        "required": ["version_dir", "width", "height", "duration_s"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "render_hyperframes_project",
                    "description": "对指定 HyperFrames 子工程执行真实 MP4 渲染。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "project_dir": {"type": "string"},
                            "output": {"type": "string"},
                            "fps": {"type": "integer", "minimum": 12, "maximum": 60},
                            "quality": {"type": "string"},
                            "timeout_sec": {"type": "integer", "minimum": 30, "maximum": 7200},
                        },
                        "required": ["project_dir", "output"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "register_version",
                    "description": "注册已经通过验收的版本，要求 preview.mp4 已存在。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "version_dir": {"type": "string"},
                            "preview": {"type": "string"},
                        },
                        "required": ["version_dir"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "register_local_render",
                    "description": "将已经完成设计的 HyperFrames 工程注册为待用户本地真实渲染版本。服务器默认必须使用此工具，不要调用服务器渲染。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "version_dir": {"type": "string"},
                            "fps": {"type": "integer", "minimum": 15, "maximum": 60},
                        },
                        "required": ["version_dir"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_chat",
                    "description": "向用户聊天栏写一条 agent 消息，可用于阶段更新、提问或最终说明。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string"},
                        },
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
        if allow_render_tools:
            return base_tools
        allowed = {"read_state", "list_files", "read_file", "write_chat", "report_progress"}
        return [tool for tool in base_tools if tool["function"]["name"] in allowed]

    def _execute_tool_call(
        self, job_id: str, prepared: PreparedSource, name: str, raw_arguments: str
    ) -> dict[str, Any]:
        try:
            args = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"工具参数 JSON 解析失败：{exc}"}

        handlers = {
            "read_state": lambda: self._tool_read_state(job_id, prepared),
            "list_files": lambda: self._tool_list_files(job_id, str(args["path"])),
            "read_file": lambda: self._tool_read_file(job_id, str(args["path"]), int(args.get("max_chars") or 16000)),
            "write_file": lambda: self._tool_write_file(job_id, str(args["path"]), str(args["content"])),
            "make_dir": lambda: self._tool_make_dir(job_id, str(args["path"])),
            "run_command": lambda: self._tool_run_command(
                job_id,
                str(args["command"]),
                str(args.get("cwd") or ""),
                int(args.get("timeout_sec") or 900),
            ),
            "report_progress": lambda: self._tool_report_progress(
                job_id,
                float(args["progress"]),
                str(args["step"]),
                str(args.get("status") or ""),
            ),
            "write_analysis": lambda: self._tool_write_analysis(job_id, args["payload"]),
            "write_edit_plan": lambda: self._tool_write_edit_plan(job_id, args["payload"]),
            "create_version_dir": lambda: self._tool_create_version_dir(job_id, str(args.get("version_id") or "")),
            "bootstrap_hyperframes_project": lambda: self._tool_bootstrap_hyperframes_project(
                job_id,
                str(args["version_dir"]),
                int(args["width"]),
                int(args["height"]),
                float(args["duration_s"]),
            ),
            "render_hyperframes_project": lambda: self._tool_render_hyperframes_project(
                job_id,
                str(args["project_dir"]),
                str(args["output"]),
                int(args.get("fps") or 30),
                str(args.get("quality") or "draft"),
                int(args.get("timeout_sec") or 1800),
            ),
            "register_version": lambda: self._tool_register_version(
                job_id,
                str(args["version_dir"]),
                str(args.get("preview") or ""),
            ),
            "register_local_render": lambda: self._tool_register_local_render(
                job_id,
                str(args["version_dir"]),
                int(args.get("fps") or 24),
            ),
            "write_chat": lambda: self._tool_write_chat(job_id, str(args["content"]), str(args.get("status") or "chat")),
        }
        if name not in handlers:
            return {"ok": False, "error": f"未知工具：{name}"}
        try:
            return handlers[name]()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _tool_read_state(self, job_id: str, prepared: PreparedSource) -> dict[str, Any]:
        snapshot = store.snapshot()
        job = snapshot["jobs"][job_id]
        pid = job["project_id"]
        project = snapshot["projects"].get(pid)
        assets = [store.public_asset(a) for a in snapshot["assets"].values() if a["project_id"] == pid]
        versions = [store.public_version(v) for v in snapshot["versions"].values() if v["project_id"] == pid]
        versions.sort(key=lambda v: int(v.get("version_number", 0)), reverse=True)
        chat = [store.public_chat_message(c) for c in snapshot.get("chat", {}).get(pid, [])][-20:]
        return {
            "ok": True,
            "project": store.public_project(project or {}),
            "settings": store.public_settings(snapshot["settings"]),
            "job": store.public_job(job),
            "assets": assets,
            "versions": versions,
            "chat": chat,
            "prepared_source": {
                "mode": prepared.mode,
                "source_dir": str(prepared.source_dir),
                "source_text_preview": _trim(prepared.source_text, 2000),
                "source_audio_path": str(prepared.source_audio_path) if prepared.source_audio_path else None,
                "transcript_path": str(prepared.transcript_path) if prepared.transcript_path else None,
                "scene_seed_path": str(prepared.scene_seed_path) if prepared.scene_seed_path else None,
                "metadata_path": str(prepared.metadata_path),
            },
            "paths": {
                "project_dir": str(store.project_dir(pid)),
                "upload_dir": str(store.upload_dir(pid)),
                "job_workspace": str(store.RUNTIME / "jobs" / job_id),
                "app_root": str(store.ROOT),
                "hyperframes_root": str(HYPERFRAMES_ROOT),
                "science_workflow": str(SCIENCE_WORKFLOW_DOC),
                "audio_materializer": str(store.ROOT / "backend" / "app" / "materialize_audio_from_seed.py"),
            },
        }

    def _tool_list_files(self, job_id: str, path: str) -> dict[str, Any]:
        resolved = self._resolve_read_path(job_id, path)
        if not resolved.exists():
            return {"ok": False, "error": f"路径不存在：{resolved}"}
        if resolved.is_file():
            return {"ok": True, "path": str(resolved), "entries": [{"name": resolved.name, "type": "file"}]}
        entries = []
        for item in sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))[:300]:
            entries.append(
                {
                    "name": item.name,
                    "type": "dir" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                }
            )
        return {"ok": True, "path": str(resolved), "entries": entries}

    def _tool_read_file(self, job_id: str, path: str, max_chars: int) -> dict[str, Any]:
        resolved = self._resolve_read_path(job_id, path)
        if not resolved.is_file():
            return {"ok": False, "error": f"不是文件：{resolved}"}
        text = resolved.read_text(encoding="utf-8", errors="replace")
        clipped = text[: max(200, min(max_chars, 50000))]
        return {"ok": True, "path": str(resolved), "content": clipped, "truncated": len(clipped) < len(text)}

    def _tool_write_file(self, job_id: str, path: str, content: str) -> dict[str, Any]:
        resolved = self._resolve_write_path(job_id, path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(resolved), "bytes": resolved.stat().st_size}

    def _tool_make_dir(self, job_id: str, path: str) -> dict[str, Any]:
        resolved = self._resolve_write_path(job_id, path)
        resolved.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(resolved)}

    def _tool_run_command(self, job_id: str, command: str, cwd: str, timeout_sec: int) -> dict[str, Any]:
        if not command.strip():
            return {"ok": False, "error": "空命令"}
        lowered = f" {command.lower()} "
        for snippet in FORBIDDEN_CMD_SNIPPETS:
            if snippet in lowered:
                return {"ok": False, "error": f"命令被安全策略阻止：{snippet.strip()}"}
        if "hyperframes init" in lowered:
            return {"ok": False, "error": "不要直接运行 hyperframes init。请改用 bootstrap_hyperframes_project 工具。"}
        workdir = self._resolve_command_cwd(job_id, cwd)
        env = os.environ.copy()
        env["FRAMECRAFT_PROJECT_ID"] = store.snapshot()["jobs"][job_id]["project_id"]
        env["FRAMECRAFT_JOB_ID"] = job_id
        env["FRAMECRAFT_AGENT_MODE"] = "openjiuwen"
        self._append_log(job_id, f"$ ({workdir}) {command}")
        proc = subprocess.run(
            command,
            shell=True,
            executable="/bin/bash",
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            encoding="utf-8",
            errors="replace",
        )
        stdout = _trim(proc.stdout or "", 10000)
        stderr = _trim(proc.stderr or "", 10000)
        tail = stdout if stdout else stderr
        if tail:
            self._append_log(job_id, _trim(tail, 1800))
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "cwd": str(workdir),
            "stdout": stdout,
            "stderr": stderr,
        }

    def _tool_report_progress(self, job_id: str, progress: float, step: str, status: str) -> dict[str, Any]:
        message = f"{progress:.0f}% {step}".strip()

        def op(data):
            job = data["jobs"][job_id]
            job["progress"] = max(0.0, min(100.0, progress))
            job["current_step"] = step
            if status:
                job["status"] = status
            job["updated_at"] = store.now_iso()
            job.setdefault("logs", []).append(message)
            return job

        job = store.mutate(op)
        return {"ok": True, "job": store.public_job(job)}

    def _tool_write_analysis(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        pid = store.snapshot()["jobs"][job_id]["project_id"]
        out = store.project_dir(pid) / "analysis" / "analysis.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "path": str(out)}

    def _tool_write_edit_plan(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        pid = store.snapshot()["jobs"][job_id]["project_id"]
        out = store.project_dir(pid) / "analysis" / "edit_plan.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        def op(data):
            for asset in data["assets"].values():
                if asset["project_id"] == pid:
                    asset["analysis_status"] = "completed"
            data["projects"][pid]["status"] = "planning"
            data["projects"][pid]["updated_at"] = store.now_iso()

        store.mutate(op)
        return {"ok": True, "path": str(out)}

    def _tool_create_version_dir(self, job_id: str, version_id: str) -> dict[str, Any]:
        pid = store.snapshot()["jobs"][job_id]["project_id"]
        vid = version_id.strip() or store.new_id("ver")
        version_dir = store.project_dir(pid) / vid
        version_dir.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "version_id": vid, "version_dir": str(version_dir)}

    def _tool_bootstrap_hyperframes_project(
        self,
        job_id: str,
        version_dir: str,
        width: int,
        height: int,
        duration_s: float,
    ) -> dict[str, Any]:
        vdir = self._resolve_write_path(job_id, version_dir)
        vdir.mkdir(parents=True, exist_ok=True)
        hf_dir = vdir / "hyperframes"
        (hf_dir / "compositions").mkdir(parents=True, exist_ok=True)
        (hf_dir / "compositions" / "components").mkdir(parents=True, exist_ok=True)
        (hf_dir / "assets").mkdir(parents=True, exist_ok=True)

        source_assets = vdir / "assets"
        copied_items: list[str] = []
        if source_assets.is_dir():
            for item in sorted(source_assets.iterdir(), key=lambda p: p.name.lower()):
                dest = hf_dir / "assets" / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
                copied_items.append(item.name)

        audio_meta_src = vdir / "audio_meta.json"
        if audio_meta_src.is_file():
            shutil.copy2(audio_meta_src, hf_dir / "audio_meta.json")

        package_json = {
            "name": "hyperframes",
            "private": True,
            "type": "module",
            "scripts": {
                "render": "npx hyperframes render",
                "check": "npx hyperframes lint && npx hyperframes validate",
            },
        }
        (hf_dir / "package.json").write_text(
            json.dumps(package_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        hyperframes_json = {
            "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
            "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
            "paths": {
                "blocks": "compositions",
                "components": "compositions/components",
                "assets": "assets",
            },
        }
        (hf_dir / "hyperframes.json").write_text(
            json.dumps(hyperframes_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        root_html = f"""<!doctype html>
<html>
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={width}, height={height}" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{
        margin: 0;
        padding: 0;
        box-sizing: border-box;
      }}
      html,
      body {{
        width: {width}px;
        height: {height}px;
        overflow: hidden;
        background: #060913;
      }}
      body {{
        font-family: "Inter", "PingFang SC", "Microsoft YaHei", sans-serif;
      }}
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="{duration_s:.3f}"
      data-width="{width}"
      data-height="{height}"
    ></div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      window.__timelines.main = tl;
    </script>
  </body>
</html>
"""
        (hf_dir / "index.html").write_text(root_html, encoding="utf-8")

        return {
            "ok": True,
            "project_dir": str(hf_dir),
            "index_path": str(hf_dir / "index.html"),
            "assets_dir": str(hf_dir / "assets"),
            "copied_items": copied_items,
        }

    def _tool_render_hyperframes_project(
        self,
        job_id: str,
        project_dir: str,
        output: str,
        fps: int,
        quality: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        job = store.snapshot()["jobs"].get(job_id) or {}
        if self._render_target(job) == "local":
            return {
                "ok": False,
                "error": "当前项目使用浏览器本地渲染。请完成 HyperFrames 工程后调用 register_local_render。",
            }
        hf_dir = self._resolve_read_path(job_id, project_dir)
        if not hf_dir.is_dir():
            return {"ok": False, "error": f"不是目录：{hf_dir}"}
        out_path = Path(output).expanduser()
        if not out_path.is_absolute():
            out_path = (hf_dir / out_path).resolve()
        else:
            out_path = out_path.resolve()
        if not _is_under(out_path, hf_dir.parent):
            return {"ok": False, "error": f"输出路径越界：{out_path}"}
        render_quality = (quality or "draft").strip().lower()
        if render_quality not in RENDER_QUALITY_SET:
            return {"ok": False, "error": f"不支持的 quality：{quality}"}
        hyperframes_cli = self._hyperframes_cli_command()
        render_flags: list[str] = []
        workers = os.getenv("FRAMECRAFT_RENDER_WORKERS", "").strip()
        if workers:
            try:
                render_flags.extend(["--workers", str(max(1, int(workers)))])
            except ValueError:
                return {"ok": False, "error": "FRAMECRAFT_RENDER_WORKERS 必须是正整数。"}
        if os.getenv("FRAMECRAFT_LOW_MEMORY_RENDER", "").strip().lower() in {"1", "true", "yes", "on"}:
            render_flags.extend(["--low-memory-mode", "--no-browser-gpu", "--quiet"])
        extra_flags = " ".join(shlex.quote(flag) for flag in render_flags)
        command = (
            f'HYPERFRAMES_NO_TELEMETRY=1 {hyperframes_cli} render '
            f'--output {shlex.quote(str(out_path))} '
            f'--fps {int(fps)} '
            f'--quality {shlex.quote(render_quality)} '
            f'--strict {extra_flags}'
        )
        result = self._tool_run_command(job_id, command, str(hf_dir), timeout_sec)
        combined = "\n".join(
            part for part in [str(result.get("stdout") or "").strip(), str(result.get("stderr") or "").strip()] if part
        )
        hard_errors = [
            line.strip()
            for line in combined.splitlines()
            if line.strip().startswith("✗ ") or line.strip().startswith("✗[")
        ]
        warnings = [
            line.strip()
            for line in combined.splitlines()
            if line.strip().startswith("⚠ ") or line.strip().startswith("⚠[")
        ]
        if hard_errors:
            result["ok"] = False
            result["error"] = "HyperFrames 渲染器报告了必须修复的硬错误。"
            result["hard_errors"] = hard_errors[:20]
            if out_path.is_file():
                result["preview_exists"] = True
                result["preview_path"] = str(out_path)
        if warnings:
            result["warnings"] = warnings[:20]
        return result

    def _tool_register_version(self, job_id: str, version_dir: str, preview: str) -> dict[str, Any]:
        snapshot = store.snapshot()
        pid = snapshot["jobs"][job_id]["project_id"]
        vdir = self._resolve_write_path(job_id, version_dir)
        preview_path = self._resolve_read_path(job_id, preview or str(vdir / "preview.mp4"))
        if not preview_path.is_file():
            raise RuntimeError(f"preview 不存在：{preview_path}")
        ensure_version_subtitles(pid, vdir)
        hyperframes_dir = vdir / "hyperframes"
        hyperframes_zip = None
        if hyperframes_dir.is_dir():
            hyperframes_zip = vdir / "hyperframes_project.zip"
            if hyperframes_zip.exists():
                hyperframes_zip.unlink()
            shutil.make_archive(str(hyperframes_zip.with_suffix("")), "zip", root_dir=vdir, base_dir="hyperframes")

        def op(data):
            existing = [v for v in data["versions"].values() if v["project_id"] == pid]
            number = max([int(v.get("version_number", 0)) for v in existing] or [0]) + 1
            vid = vdir.name
            version = {
                "id": vid,
                "project_id": pid,
                "version_number": number,
                "preview_url": f"/api/projects/{pid}/versions/{vid}/preview",
                "draft_url": None,
                "timeline_url": f"/api/projects/{pid}/versions/{vid}/timeline",
                "subtitles_url": f"/api/projects/{pid}/versions/{vid}/subtitles",
                "source_ledger_url": f"/api/projects/{pid}/versions/{vid}/source-ledger" if (vdir / "SOURCE_LEDGER.md").is_file() else None,
                "cover_url": None,
                "publish_copy_url": None,
                "hyperframes_url": f"/api/projects/{pid}/versions/{vid}/hyperframes" if hyperframes_zip else None,
                "version_dir": str(vdir),
                "preview_path": str(preview_path),
                "draft_path": None,
                "draft_dir": None,
                "import_guide_path": None,
                "created_at": store.now_iso(),
            }
            data["versions"][vid] = version
            project = data["projects"][pid]
            project["current_version_id"] = vid
            project["status"] = "completed"
            project["updated_at"] = store.now_iso()
            return version

        version = store.mutate(op)
        return {"ok": True, "version": store.public_version(version)}

    def _tool_register_local_render(self, job_id: str, version_dir: str, fps: int = 24) -> dict[str, Any]:
        snapshot = store.snapshot()
        job = snapshot["jobs"][job_id]
        pid = job["project_id"]
        vdir = self._resolve_write_path(job_id, version_dir)
        hyperframes_dir = vdir / "hyperframes"
        index_path = hyperframes_dir / "index.html"
        timeline_path = vdir / "timeline.json"
        if not index_path.is_file() or not timeline_path.is_file():
            raise RuntimeError("浏览器渲染工程缺少 hyperframes/index.html 或 timeline.json。")
        ensure_version_subtitles(pid, vdir)
        timeline = _safe_json(timeline_path)
        expected_duration = float(
            timeline.get("total_duration") or timeline.get("duration_s") or timeline.get("duration") or 0
        )
        if expected_duration <= 0:
            root_match = re.search(r'data-duration="([0-9.]+)"', index_path.read_text(encoding="utf-8"))
            expected_duration = float(root_match.group(1)) if root_match else 0
        if expected_duration <= 0:
            raise RuntimeError("无法从浏览器渲染工程确定成片时长。")
        manifest = {
            "schema_version": 1,
            "project_id": pid,
            "version_id": vdir.name,
            "fps": max(15, min(int(fps), 60)),
            "quality": "standard",
            "expected_duration_s": round(expected_duration, 3),
            "renderer": "hyperframes-strict",
            "scene_samples": [
                {
                    "scene_number": scene.get("scene_number"),
                    "start_s": scene.get("start_time"),
                    "end_s": scene.get("end_time"),
                    "headline": scene.get("headline"),
                    "semantic_motion": scene.get("semantic_motion"),
                }
                for scene in timeline.get("scenes") or []
                if float(scene.get("end_time") or 0) > float(scene.get("start_time") or 0)
            ],
            "contact_sample_strategy": "entry_mid_late_per_scene",
        }
        (vdir / "local_render_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (hyperframes_dir / "local-render-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        bundle = vdir / "hyperframes_project.zip"
        bundle.unlink(missing_ok=True)
        shutil.make_archive(str(bundle.with_suffix("")), "zip", root_dir=vdir, base_dir="hyperframes")

        def op(data):
            existing = [v for v in data["versions"].values() if v["project_id"] == pid]
            number = max([int(v.get("version_number", 0)) for v in existing] or [0]) + 1
            vid = vdir.name
            version = {
                "id": vid,
                "project_id": pid,
                "version_number": number,
                "status": "awaiting_local_render",
                "preview_url": None,
                "draft_url": None,
                "timeline_url": f"/api/projects/{pid}/versions/{vid}/timeline",
                "subtitles_url": f"/api/projects/{pid}/versions/{vid}/subtitles",
                "cover_url": None,
                "publish_copy_url": None,
                "hyperframes_url": f"/api/projects/{pid}/versions/{vid}/hyperframes",
                "local_render_bundle_url": f"/api/projects/{pid}/versions/{vid}/hyperframes",
                "render_fps": manifest["fps"],
                "expected_duration_s": manifest["expected_duration_s"],
                "visual_revision_round": int(
                    (((data["jobs"][job_id].get("payload") or {}).get("patch") or {}).get("visual_revision_round") or 0)
                ),
                "version_dir": str(vdir),
                "preview_path": None,
                "created_at": store.now_iso(),
            }
            data["versions"][vid] = version
            data["projects"][pid]["status"] = "awaiting_local_render"
            data["projects"][pid]["updated_at"] = store.now_iso()
            data["jobs"][job_id]["result"] = {
                "render_target": "local",
                "version_id": vid,
                "bundle_url": version["local_render_bundle_url"],
                "fps": manifest["fps"],
                "expected_duration_s": manifest["expected_duration_s"],
            }
            return version

        version = store.mutate(op)
        return {"ok": True, "version": store.public_version(version), "manifest": manifest}

    def review_local_render(
        self,
        project_id: str,
        version_id: str,
        contact_sheet: Path,
        media_validation: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = store.snapshot()
        version = snapshot["versions"].get(version_id)
        if not version or version.get("project_id") != project_id:
            raise RuntimeError("本地渲染版本不存在。")
        if version.get("status") not in {"awaiting_local_render", "local_render_failed", "local_render_ready"}:
            raise RuntimeError("该版本当前不等待本地渲染结果。")
        version_dir = Path(version["version_dir"])
        expected_duration = float(version.get("expected_duration_s") or 0)
        try:
            actual_duration = float(media_validation.get("duration_s") or 0)
            if not actual_duration or abs(actual_duration - expected_duration) > max(0.8, expected_duration * 0.02):
                raise RuntimeError(
                    f"本地成片时长异常：期望 {expected_duration:.2f} 秒，实际 {actual_duration:.2f} 秒。"
                )
            project = snapshot["projects"].get(project_id) or {}
            creative_plan = _safe_json(store.project_dir(project_id) / "analysis" / "creative_plan.json")
            timeline = _safe_json(version_dir / "timeline.json")
            transcript_path = store.project_dir(project_id) / "input" / "transcript.txt"
            source_text = transcript_path.read_text(encoding="utf-8", errors="replace") if transcript_path.is_file() else ""
            saved_sheet = version_dir / "agent-visual-contact-sheet.jpg"
            shutil.copy2(contact_sheet, saved_sheet)
            try:
                review = run_visual_review(
                    saved_sheet,
                    {
                        "project": project.get("name"),
                        "input_mode": project.get("input_mode"),
                        "requires_source_labels": project.get("input_mode") == "topic",
                        "duration_s": expected_duration,
                        "source_transcript": source_text,
                        "scenes": timeline.get("scenes") or creative_plan.get("scenes") or [],
                    },
                )
            finally:
                saved_sheet.unlink(missing_ok=True)
            review["media_validation"] = media_validation
            review["review_is_model_generated"] = True
            (version_dir / "agent_visual_review.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if not review.get("pass") or float(review.get("score") or 0) < 82:
                scene_numbers = self._review_scene_numbers(review, timeline, creative_plan)
                return self._hold_failed_local_render(project_id, version_id, review, scene_numbers)
        except Exception as exc:
            review = {
                "pass": False,
                "score": 0,
                "summary": "验收过程未完成",
                "issues": [str(exc)],
                "media_validation": media_validation,
                "review_is_model_generated": False,
            }
            (version_dir / "agent_visual_review.json").write_text(
                json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return self._hold_failed_local_render(project_id, version_id, review, [])

        def op(data):
            current = data["versions"][version_id]
            current["status"] = "local_render_ready"
            current.pop("review_error", None)
            current["review_score"] = float(review.get("score") or 0)
            current["review_issues"] = review.get("issues") or []
            current["preview_path"] = None
            current["preview_url"] = None
            project_data = data["projects"][project_id]
            project_data["current_version_id"] = None
            project_data["status"] = "ready_to_render"
            project_data["updated_at"] = store.now_iso()
            data.setdefault("chat", {}).setdefault(project_id, []).append(
                {
                    "id": store.new_id("msg"),
                    "project_id": project_id,
                    "role": "agent",
                    "content": "本机 HyperFrames 成片已通过完整时长、音视频流和视觉 Agent 验收；MP4 只保留在当前用户电脑，服务器仅保存可复渲染工程。",
                    "status": "done",
                    "created_at": store.now_iso(),
                }
            )
            return current

        return store.public_version(store.mutate(op))

    @staticmethod
    def _review_issue_texts(review: dict[str, Any]) -> list[str]:
        issue_texts = []
        for issue in review.get("issues") or []:
            if isinstance(issue, dict):
                issue_texts.append(
                    str(
                        issue.get("desc")
                        or issue.get("detail")
                        or issue.get("message")
                        or json.dumps(issue, ensure_ascii=False)
                    )
                )
            else:
                issue_texts.append(str(issue))
        return [text.strip() for text in issue_texts if text.strip()]

    @staticmethod
    def _review_scene_numbers(
        review: dict[str, Any], timeline: dict[str, Any], creative_plan: dict[str, Any]
    ) -> list[int]:
        issue_blob = json.dumps(review.get("issues") or [], ensure_ascii=False)
        scene_numbers = sorted({int(value) for value in re.findall(r"(?:场景|第)\s*(\d+)\s*(?:幕|页)?", issue_blob)})
        if scene_numbers:
            return scene_numbers
        return [
            int(scene.get("scene_number") or index + 1)
            for index, scene in enumerate(timeline.get("scenes") or creative_plan.get("scenes") or [])
        ]

    def _hold_failed_local_render(
        self,
        project_id: str,
        version_id: str,
        review: dict[str, Any],
        scene_numbers: list[int],
    ) -> dict[str, Any]:
        score = float(review.get("score") or 0)
        issue_texts = self._review_issue_texts(review)
        summary = str(review.get("summary") or "质量未达到验收标准")
        reason = f"视觉 Agent 验收未通过：{'; '.join(issue_texts or [summary])}"
        issue_lines = "\n".join(f"{index}. {text}" for index, text in enumerate(issue_texts or [summary], start=1))
        content = (
            f"本次成片验收得分：{score:g}/100，未达到 82 分通过线。\n\n"
            f"发现的问题：\n{issue_lines}\n\n"
            "成片已保留在中间页面，可以直接播放检查。是否根据这些问题重新生成？"
        )

        def op(data):
            current = data["versions"][version_id]
            current["status"] = "local_render_failed"
            current["review_error"] = reason
            current["review_score"] = score
            current["review_issues"] = review.get("issues") or []
            project = data["projects"][project_id]
            project["status"] = "awaiting_retry_decision"
            project["updated_at"] = store.now_iso()
            data.setdefault("chat", {}).setdefault(project_id, []).append(
                {
                    "id": store.new_id("msg"),
                    "project_id": project_id,
                    "role": "agent",
                    "content": content,
                    "status": "awaiting_retry",
                    "action": "retry_render",
                    "version_id": version_id,
                    "created_at": store.now_iso(),
                }
            )

        store.mutate(op)
        return {
            "ok": False,
            "status": "awaiting_user_retry",
            "review": review,
            "revision_scene_numbers": scene_numbers,
            "version_id": version_id,
        }

    def retry_local_render(self, project_id: str, version_id: str) -> dict[str, Any]:
        snapshot = store.snapshot()
        version = snapshot["versions"].get(version_id)
        if not version or version.get("project_id") != project_id:
            raise RuntimeError("待重试的版本不存在。")
        if version.get("status") != "local_render_failed":
            raise RuntimeError("该版本当前没有等待重试。")
        version_dir = Path(version["version_dir"])
        review = _safe_json(version_dir / "agent_visual_review.json")
        if not review:
            review = {
                "pass": False,
                "score": version.get("review_score") or 0,
                "summary": version.get("review_error") or "上一版未通过验收",
                "issues": version.get("review_issues") or [],
            }
        timeline = _safe_json(version_dir / "timeline.json")
        creative_plan = _safe_json(store.project_dir(project_id) / "analysis" / "creative_plan.json")
        scene_numbers = self._review_scene_numbers(review, timeline, creative_plan)
        revision_round = int(version.get("visual_revision_round") or 0) + 1
        job = self.start(
            project_id,
            "apply_patch",
            {
                "fps": int(version.get("render_fps") or 24),
                "render_target": "local",
                "patch": {
                    "regenerate_scene_code": True,
                    "visual_review": review,
                    "revision_scene_numbers": scene_numbers,
                    "rejected_version_id": version_id,
                    "visual_revision_round": revision_round,
                },
            },
        )

        def op(data):
            for message in data.setdefault("chat", {}).setdefault(project_id, []):
                if message.get("action") == "retry_render" and message.get("version_id") == version_id:
                    message["action"] = None
                    message["status"] = "retrying"
            data["chat"][project_id].append(
                {
                    "id": store.new_id("msg"),
                    "project_id": project_id,
                    "role": "agent",
                    "content": "已开始重试。上一版的验收分数和问题已完整反馈给提示词 AI，正在重新设计相关页面。",
                    "status": "running",
                    "job_id": job["id"],
                    "created_at": store.now_iso(),
                }
            )

        store.mutate(op)
        return job

    def regenerate_from_chat(self, project_id: str, version_id: str, message_id: str | None = None) -> dict[str, Any]:
        action_payload = self._find_chat_action_payload(project_id, version_id, message_id, "regenerate_video")
        version = store.snapshot()["versions"].get(version_id)
        if not version or version.get("project_id") != project_id:
            raise RuntimeError("待重新生成的版本不存在。")
        revision_round = int(version.get("visual_revision_round") or 0) + 1
        job = self.start(
            project_id,
            "apply_patch",
            {
                "fps": int(version.get("render_fps") or 24),
                "render_target": "local",
                "patch": {
                    "chat_revision_request": action_payload,
                    "regenerate_all": True,
                    "base_version_id": version_id,
                    "visual_revision_round": revision_round,
                },
            },
        )
        self._consume_chat_action(project_id, version_id, message_id, "regenerate_video")
        self._append_action_started(
            project_id,
            job["id"],
            "已开始重新生成。对话 AI 已把你的要求整理给提示词 AI，接下来会重新规划全片并生成新的 HyperFrames 工程。",
        )
        return job

    def fine_tune_from_chat(self, project_id: str, version_id: str, message_id: str | None = None) -> dict[str, Any]:
        action_payload = self._find_chat_action_payload(project_id, version_id, message_id, "fine_tune_video")
        version = store.snapshot()["versions"].get(version_id)
        if not version or version.get("project_id") != project_id:
            raise RuntimeError("待微调的版本不存在。")
        job = self.start(
            project_id,
            "fine_tune",
            {
                "fps": int(version.get("render_fps") or 24),
                "base_version_id": version_id,
                "chat_revision_request": action_payload,
            },
        )
        self._consume_chat_action(project_id, version_id, message_id, "fine_tune_video")
        self._append_action_started(
            project_id,
            job["id"],
            "已开始微调。对话 AI 会读取当前工程代码，只改必要文件，然后重新交给当前浏览器渲染和验收。",
        )
        return job

    def retry_unplayable_render(
        self,
        project_id: str,
        version_id: str,
        error_message: str,
        attempt: int,
        media_validation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if attempt < 1 or attempt > 3:
            raise RuntimeError("不可播放自动重试次数必须在 1 到 3 之间。")
        snapshot = store.snapshot()
        version = snapshot["versions"].get(version_id)
        if not version or version.get("project_id") != project_id:
            raise RuntimeError("待修复的版本不存在。")
        if version.get("status") not in {"awaiting_local_render", "local_render_failed", "local_render_ready"}:
            raise RuntimeError("该版本当前不适合做不可播放自动修复。")
        review = {
            "pass": False,
            "score": 0,
            "summary": "本机没有生成可播放视频",
            "issues": [error_message],
            "media_validation": media_validation or {},
            "review_is_model_generated": False,
            "auto_retry_attempt": attempt,
        }
        revision_round = int(version.get("visual_revision_round") or 0) + 1
        job = self.start(
            project_id,
            "apply_patch",
            {
                "fps": int(version.get("render_fps") or 24),
                "render_target": "local",
                "patch": {
                    "regenerate_scene_code": True,
                    "visual_review": review,
                    "revision_scene_numbers": [],
                    "rejected_version_id": version_id,
                    "visual_revision_round": revision_round,
                    "unplayable_auto_retry_attempt": attempt,
                },
            },
        )

        def op(data):
            current = data["versions"].get(version_id)
            if current:
                current["status"] = "local_render_failed"
                current["review_error"] = "本机没有生成可播放视频：" + error_message
                current["review_score"] = 0
                current["review_issues"] = [error_message]
                current["unplayable_retry_attempt"] = attempt
            data.setdefault("chat", {}).setdefault(project_id, []).append(
                {
                    "id": store.new_id("msg"),
                    "project_id": project_id,
                    "role": "agent",
                    "content": f"本机没有生成可播放视频，已自动启动第 {attempt}/3 次工程修复。原因：{error_message}",
                    "status": "running",
                    "job_id": job["id"],
                    "created_at": store.now_iso(),
                }
            )

        store.mutate(op)
        return job

    def _append_chat_action(
        self,
        project_id: str,
        content: str,
        status: str,
        action: str | None,
        version_id: str | None,
        action_payload: dict[str, Any],
    ) -> dict[str, Any]:
        message = {
            "id": store.new_id("msg"),
            "project_id": project_id,
            "role": "agent",
            "content": content,
            "status": status,
            "action": action,
            "version_id": version_id,
            "action_payload": action_payload,
            "created_at": store.now_iso(),
        }

        def op(data):
            data.setdefault("chat", {}).setdefault(project_id, []).append(message)
            return message

        return store.public_chat_message(store.mutate(op))

    def _consume_chat_action(
        self,
        project_id: str,
        version_id: str,
        message_id: str | None,
        expected_action: str,
    ) -> dict[str, Any]:
        def op(data):
            messages = data.setdefault("chat", {}).setdefault(project_id, [])
            candidates = [
                message for message in messages
                if message.get("action") == expected_action and message.get("version_id") == version_id
            ]
            if message_id:
                candidates = [message for message in candidates if message.get("id") == message_id]
            if not candidates:
                raise RuntimeError("没有找到等待确认的对话动作。")
            message = candidates[-1]
            message["action"] = None
            message["status"] = "action_accepted"
            return dict(message.get("action_payload") or {})

        return store.mutate(op)

    def _find_chat_action_payload(
        self,
        project_id: str,
        version_id: str,
        message_id: str | None,
        expected_action: str,
    ) -> dict[str, Any]:
        messages = store.snapshot().setdefault("chat", {}).setdefault(project_id, [])
        candidates = [
            message for message in messages
            if message.get("action") == expected_action and message.get("version_id") == version_id
        ]
        if message_id:
            candidates = [message for message in candidates if message.get("id") == message_id]
        if not candidates:
            raise RuntimeError("没有找到等待确认的对话动作。")
        return dict((candidates[-1]).get("action_payload") or {})

    def _append_action_started(self, project_id: str, job_id: str, content: str) -> None:
        def op(data):
            data.setdefault("chat", {}).setdefault(project_id, []).append(
                {
                    "id": store.new_id("msg"),
                    "project_id": project_id,
                    "role": "agent",
                    "content": content,
                    "status": "running",
                    "job_id": job_id,
                    "created_at": store.now_iso(),
                }
            )

        store.mutate(op)

    def _latest_interactive_version(
        self, project_id: str, snapshot: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        data = snapshot or store.snapshot()
        versions = [
            version
            for version in data.get("versions", {}).values()
            if version.get("project_id") == project_id
            and version.get("status") in {"local_render_ready", "local_render_failed", "completed"}
        ]
        versions.sort(key=lambda version: (int(version.get("version_number") or 0), version.get("created_at") or ""))
        return versions[-1] if versions else None

    def _dialogue_context(
        self,
        project_id: str,
        project: dict[str, Any],
        version: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        version_dir = Path(str(version.get("version_dir") or ""))
        hyperframes_dir = version_dir / "hyperframes"
        timeline = _safe_json(version_dir / "timeline.json")
        review = _safe_json(version_dir / "agent_visual_review.json")
        file_summaries = []
        for path in sorted(hyperframes_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js", ".json", ".svg"}:
                continue
            relative = path.relative_to(version_dir).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            file_summaries.append({"path": relative, "chars": len(text), "preview": _trim(text, 6000)})
            if len(file_summaries) >= 10:
                break
        return {
            "app_requirements": {
                "product": "一键科普视频生成",
                "render_policy": "服务器保存 HyperFrames 工程，用户浏览器本地真实渲染 MP4。",
                "subtitles": "简体中文字幕固定居中放在底部安全区，淡入淡出。",
                "quality_gate": "可播放视频之外的验收问题先展示分数和问题，由用户决定是否重新生成或微调。",
            },
            "project": store.public_project(project),
            "latest_version": store.public_version(version),
            "timeline": timeline,
            "visual_review": review,
            "recent_chat": [store.public_chat_message(c) for c in snapshot.get("chat", {}).get(project_id, [])][-12:],
            "hyperframes_files": file_summaries,
        }

    def _run_dialogue_fine_tune(self, job_id: str, prepared: PreparedSource) -> None:
        started = time.perf_counter()
        snapshot = store.snapshot()
        job = snapshot["jobs"][job_id]
        project_id = job["project_id"]
        project = snapshot["projects"].get(project_id) or {}
        payload = job.get("payload") or {}
        base_version_id = str(payload.get("base_version_id") or "")
        base_version = snapshot["versions"].get(base_version_id)
        if not base_version or base_version.get("project_id") != project_id:
            raise RuntimeError("微调缺少可读取的基准版本。")
        base_dir = Path(str(base_version.get("version_dir") or ""))
        base_hf = base_dir / "hyperframes"
        if not (base_hf / "index.html").is_file():
            raise RuntimeError("基准版本缺少 HyperFrames 工程，无法微调。")

        self._set_step(job_id, 18, "对话 AI 正在读取当前 HyperFrames 工程")
        version_count = len([v for v in snapshot["versions"].values() if v["project_id"] == project_id])
        version_hint = f"v{version_count + 1:03d}"
        created = self._tool_create_version_dir(job_id, version_hint)
        version_dir = Path(str(created["version_dir"]))
        shutil.copytree(base_hf, version_dir / "hyperframes", dirs_exist_ok=True)
        for name in ("timeline.json", "subtitles.srt", "SOURCE_LEDGER.md", "local_render_manifest.json"):
            src = base_dir / name
            if src.is_file():
                shutil.copy2(src, version_dir / name)

        context = self._dialogue_context(project_id, project, {**base_version, "version_dir": str(version_dir)}, snapshot)
        context["action_request"] = payload.get("chat_revision_request") or {}
        context["output_contract"] = {
            "base_dir": str(version_dir),
            "allowed_paths": "只能修改 hyperframes/ 下的 html、css、js、json、svg 文件；如确实必要可修改 timeline.json。",
            "return_json": {
                "reply": "给用户看的说明",
                "changed_files": [{"path": "hyperframes/index.html", "content": "完整新文件内容"}],
                "timeline": "可选，完整 timeline.json 对象",
            },
        }
        self._set_step(job_id, 32, "对话 AI 正在制定局部微调代码补丁")
        response = create_client().chat.completions.create(
            model=deepseek_settings()["text_model"],
            messages=[
                {
                    "role": "system",
                    "content": (
                        (PROMPTS_DIR / "dialogue_ai_system.md").read_text(encoding="utf-8")
                        + "\n\n现在你处于微调执行阶段。请直接返回需要写入的完整工程文件内容。"
                        "只改满足用户要求的最少文件；保持音频、字幕时间轴、来源左下角和可渲染契约不变。"
                        "输出 JSON：reply, changed_files, timeline。"
                    ),
                },
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            temperature=0.18,
            max_tokens=14000,
            extra_body={"thinking": {"type": "disabled"}},
        )
        result = parse_json_object(response.choices[0].message.content)
        changed_files = result.get("changed_files") or []
        if not isinstance(changed_files, list) or not changed_files:
            raise RuntimeError("对话 AI 没有返回可应用的工程文件修改。")

        self._set_step(job_id, 58, "正在应用对话 AI 的工程文件修改")
        applied: list[str] = []
        for item in changed_files[:12]:
            if not isinstance(item, dict):
                continue
            relative = str(item.get("path") or "").strip().replace("\\", "/")
            content = item.get("content")
            if not relative or not isinstance(content, str):
                continue
            if relative.startswith("/") or ".." in Path(relative).parts:
                raise RuntimeError(f"微调返回了越界路径：{relative}")
            if not (
                relative.startswith("hyperframes/")
                and Path(relative).suffix.lower() in {".html", ".css", ".js", ".json", ".svg"}
            ):
                raise RuntimeError(f"微调只能修改 HyperFrames 文本工程文件：{relative}")
            target = (version_dir / relative).resolve()
            if not _is_under(target, version_dir / "hyperframes"):
                raise RuntimeError(f"微调写入路径越界：{relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            applied.append(relative)
        if not applied:
            raise RuntimeError("对话 AI 返回的修改没有可应用文件。")
        if isinstance(result.get("timeline"), dict):
            (version_dir / "timeline.json").write_text(
                json.dumps(result["timeline"], ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        self._set_step(job_id, 82, "微调工程已完成，等待当前浏览器重新渲染")
        registered = self._tool_register_local_render(
            job_id,
            str(version_dir),
            int(payload.get("fps") or base_version.get("render_fps") or 24),
        )
        if not registered.get("ok"):
            raise RuntimeError("微调版本注册失败。")
        self._tool_write_chat(
            job_id,
            (
                f"微调工程已完成，修改了 {len(applied)} 个文件：{', '.join(applied[:5])}。"
                f"耗时 {time.perf_counter() - started:.1f} 秒，接下来由当前浏览器生成 MP4 并验收。"
            ),
            "awaiting_local_render",
        )

    def _tool_write_chat(self, job_id: str, content: str, status: str) -> dict[str, Any]:
        pid = store.snapshot()["jobs"][job_id]["project_id"]
        message = {
            "id": store.new_id("msg"),
            "project_id": pid,
            "job_id": job_id,
            "role": "agent",
            "content": content,
            "status": status,
            "created_at": store.now_iso(),
        }

        def op(data):
            data.setdefault("chat", {}).setdefault(pid, []).append(message)
            return message

        store.mutate(op)
        return {"ok": True, "message": store.public_chat_message(message), "terminal": status == "needs_input"}

    def _run_managed_render(self, job_id: str, prepared: PreparedSource) -> None:
        total_started = time.perf_counter()
        snapshot = store.snapshot()
        job = snapshot["jobs"][job_id]
        project_id = job["project_id"]
        project = snapshot["projects"].get(project_id) or {}
        self._append_log(job_id, "提示词 AI 正在设计分镜，动画 AI 将逐页生成 HyperFrames 工程。", chat=True)
        self._set_step(job_id, 24, "正在创建版本目录与渲染工程")

        version_count = len([v for v in snapshot["versions"].values() if v["project_id"] == project_id])
        version_hint = f"v{version_count + 1:03d}"
        created = self._tool_create_version_dir(job_id, version_hint)
        version_dir = Path(str(created["version_dir"]))
        version_dir.mkdir(parents=True, exist_ok=True)
        assets_dir = version_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        if not prepared.source_audio_path or not prepared.source_audio_path.is_file():
            raise RuntimeError("缺少可渲染的旁白音频，无法生成科普视频。")
        audio_name = "source_audio" + prepared.source_audio_path.suffix.lower()
        shutil.copy2(prepared.source_audio_path, assets_dir / audio_name)

        width, height = _managed_dimensions(str(project.get("aspect_ratio") or "9:16"))
        scene_seed = _safe_json(prepared.scene_seed_path)
        duration_s = float(scene_seed.get("total_duration_s") or 0) or max(float(project.get("target_duration") or 0), 12.0)
        boot = self._tool_bootstrap_hyperframes_project(job_id, str(version_dir), width, height, duration_s)
        if not boot.get("ok"):
            raise RuntimeError(str(boot.get("error") or "HyperFrames 工程初始化失败。"))

        self._set_step(job_id, 32, "提示词 AI 正在编写逐页动画设计")
        patch = (job.get("payload") or {}).get("patch") or {}
        revision_feedback = None
        if patch.get("visual_review"):
            revision_feedback = {
                "rejected_version_id": patch.get("rejected_version_id"),
                "review_score": (patch.get("visual_review") or {}).get("score"),
                "review_summary": (patch.get("visual_review") or {}).get("summary"),
                "issues": (patch.get("visual_review") or {}).get("issues") or [],
                "revision_scene_numbers": patch.get("revision_scene_numbers") or [],
                "instruction": "根据验收问题重新设计对应页面，并保证修复后与全片视觉体系一致。",
            }
            if patch.get("unplayable_auto_retry_attempt"):
                revision_feedback["instruction"] = (
                    "上一版工程未能在浏览器生成可播放视频。请优先修复 HyperFrames 结构、素材引用、"
                    "时间线、尺寸、动画脚本和字幕挂载问题，保证新工程能稳定启动和编码。"
                )
                revision_feedback["unplayable_auto_retry_attempt"] = patch.get("unplayable_auto_retry_attempt")
        if patch.get("chat_revision_request"):
            chat_request = patch.get("chat_revision_request") or {}
            revision_feedback = {
                **(revision_feedback or {}),
                "rejected_version_id": patch.get("base_version_id") or patch.get("rejected_version_id"),
                "revision_mode": "full_regeneration" if patch.get("regenerate_all") else "revision",
                "user_message": chat_request.get("user_message"),
                "dialogue_ai_summary": chat_request.get("edit_summary"),
                "target_files_hint": chat_request.get("target_files_hint") or [],
                "instruction": (
                    "这是用户在初版生成后确认的重新生成需求。请把它作为全片重新规划的核心目标，"
                    "重新设计页面结构、视觉隐喻和动画节奏，同时保持原始讲稿/旁白时间轴不被改写。"
                ),
            }
        summary = materialize_two_stage_version(
            project=project,
            prepared=prepared,
            version_dir=version_dir,
            hyperframes_dir=Path(str(boot["project_dir"])),
            audio_asset_name=audio_name,
            progress=lambda progress, step: self._set_step(job_id, progress, step),
            revision_feedback=revision_feedback,
        )
        source_ledger = prepared.source_dir / "SOURCE_LEDGER.md"
        if source_ledger.is_file():
            shutil.copy2(source_ledger, version_dir / "SOURCE_LEDGER.md")
        render_fps = int((job.get("payload") or {}).get("fps") or 24)
        render_fps = max(15, min(render_fps, 60))
        if self._render_target(job) == "local":
            self._set_step(job_id, 86, "HyperFrames 工程已就绪，等待用户电脑真实渲染")
            registered = self._tool_register_local_render(job_id, str(version_dir), render_fps)
            if not registered.get("ok"):
                raise RuntimeError("本地渲染工程注册失败。")
            self._tool_write_chat(
                job_id,
                (
                    f"视频设计与 HyperFrames 工程已完成：{summary['scene_code_count']} 幕 Agent 原创源码、"
                    f"{summary['caption_count']} 条字幕，总时长约 {summary['duration_s']} 秒。"
                    "网页正在下载工程并调用浏览器内置渲染器，服务器不会执行或保存视频成片。"
                ),
                "awaiting_local_render",
            )
            return

        self._set_step(job_id, 76, "正在使用 HyperFrames 真实渲染 MP4")
        render = self._tool_render_hyperframes_project(
            job_id=job_id,
            project_dir=str(boot["project_dir"]),
            output=str(version_dir / "preview.mp4"),
            fps=render_fps,
            quality="standard",
            timeout_sec=1800,
        )
        if not render.get("ok"):
            raise RuntimeError(
                str(render.get("error") or "HyperFrames 渲染失败。")
                + ("\n" + "\n".join(render.get("hard_errors") or []) if render.get("hard_errors") else "")
            )

        self._set_step(job_id, 86, "视觉 Agent 正在检查全片抽帧")
        media = self._probe_rendered_media(version_dir / "preview.mp4", summary["duration_s"])
        sheet = self._extract_contact_sheet(version_dir / "preview.mp4", version_dir / "visual-review.jpg", summary["duration_s"])
        review = run_visual_review(
            sheet,
                {
                    "project": project.get("name"),
                    "input_mode": project.get("input_mode"),
                    "requires_source_labels": project.get("input_mode") == "topic",
                "duration_s": summary["duration_s"],
                "source_transcript": prepared.source_text,
                "scenes": summary["plan"].get("pages") or [],
            },
        )
        review["media_validation"] = media
        review["review_is_model_generated"] = True
        (version_dir / "agent_visual_review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not review.get("pass") or float(review.get("score") or 0) < 82:
            raise RuntimeError(f"视觉 Agent 验收未通过：{'; '.join(review.get('issues') or [review.get('summary') or '质量不足'])}")

        self._set_step(job_id, 94, "正在注册版本与整理下载产物")
        registered = self._tool_register_version(job_id, str(version_dir), str(version_dir / "preview.mp4"))
        if not registered.get("ok"):
            raise RuntimeError("版本注册失败。")
        self._tool_write_chat(
            job_id,
            (
                f"已完成成片生成：{summary['scene_count']} 个场景、{summary['caption_count']} 条字幕，"
                f"总时长约 {summary['duration_s']} 秒，端到端耗时 {time.perf_counter() - total_started:.1f} 秒。"
                "现在可以直接下载完整 MP4、时间线 JSON 和 HyperFrames 工程。"
            ),
            "done",
        )

    def _run_managed_analysis(self, job_id: str, prepared: PreparedSource) -> None:
        snapshot = store.snapshot()
        job = snapshot["jobs"][job_id]
        project = snapshot["projects"].get(job["project_id"]) or {}
        self._append_log(job_id, "提示词 AI 正在根据旁白生成逐页动画设计。", chat=True)
        self._set_step(job_id, 30, "提示词 AI 正在分析讲稿与页面结构")
        summary = build_two_stage_analysis(project, prepared)
        analysis_dir = store.project_dir(str(project.get("id"))) / "analysis"
        analysis_dir.mkdir(parents=True, exist_ok=True)
        (analysis_dir / "creative_plan.json").write_text(
            json.dumps(summary["plan"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (analysis_dir / "agent_trace.json").write_text(
            json.dumps({"pipeline":"two_stage_prompt_to_animation","prompt_ai":summary["trace"]}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._append_log(
            job_id,
            f"提示词 AI 已完成 {summary['scene_count']} 页独立动画设计。",
            chat=True,
        )
        summary["analysis"]["generation_pipeline"] = "two_stage_prompt_to_animation"
        analysis_result = self._tool_write_analysis(job_id, summary["analysis"])
        plan_result = self._tool_write_edit_plan(job_id, summary["edit_plan"])
        if not analysis_result.get("ok") or not plan_result.get("ok"):
            raise RuntimeError("分析文件写入失败。")
        self._set_step(job_id, 100, "分析完成")
        self._tool_write_chat(
            job_id,
            (
                f"分析已完成：识别出 {summary['scene_count']} 个场景、{summary['caption_count']} 条字幕，"
                f"预计成片时长约 {summary['duration_s']} 秒，可直接进入生成阶段。"
            ),
            "done",
        )

    def _team_payload(self, project: dict[str, Any], prepared: PreparedSource) -> dict[str, Any]:
        seed = _safe_json(prepared.scene_seed_path)
        return {
            "project": {
                "name": project.get("name"),
                "aspect_ratio": project.get("aspect_ratio"),
                "target_style": project.get("target_style"),
                "output_language": project.get("output_language"),
                "input_mode": project.get("input_mode"),
                "topic": project.get("topic"),
                "requirements": project.get("requirements"),
            },
            "transcript": prepared.source_text,
            "scenes": [
                {
                    "scene_number": item.get("sceneNumber"),
                    "start": item.get("start_s"),
                    "end": item.get("end_s"),
                    "transcript": item.get("transcript"),
                    "chapter_title": item.get("chapter_title"),
                    "visual_claim": item.get("visual_claim"),
                    "semantic_motion": item.get("motion"),
                    "evidence_ids": item.get("evidence_ids") or [],
                    "evidence_sources": item.get("evidence_sources") or [],
                }
                for item in seed.get("scenes") or []
            ],
        }

    def _probe_rendered_media(self, preview: Path, expected_duration: float) -> dict[str, Any]:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(preview)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError("成片媒体探测失败。")
        payload = json.loads(proc.stdout)
        streams = payload.get("streams") or []
        duration = float((payload.get("format") or {}).get("duration") or 0)
        if not any(item.get("codec_type") == "video" for item in streams):
            raise RuntimeError("成片缺少视频轨。")
        if not any(item.get("codec_type") == "audio" for item in streams):
            raise RuntimeError("成片缺少音频轨。")
        if abs(duration - float(expected_duration)) > 1.2:
            raise RuntimeError(f"成片时长 {duration:.2f}s 与输入 {expected_duration:.2f}s 不一致。")
        return {"pass": True, "duration_s": round(duration, 3), "has_video": True, "has_audio": True}

    def _extract_contact_sheet(self, preview: Path, output: Path, duration_s: float) -> Path:
        fps = max(0.02, 8.0 / max(float(duration_s), 1.0))
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(preview),
                "-vf", f"fps={fps:.6f},scale=480:-1,tile=4x2:padding=8:margin=8",
                "-frames:v", "1", str(output),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if proc.returncode != 0 or not output.is_file():
            raise RuntimeError(f"全片抽帧失败：{(proc.stderr or '').strip()[-600:]}")
        return output

    def _resolve_read_path(self, job_id: str, raw: str) -> Path:
        pid = store.snapshot()["jobs"][job_id]["project_id"]
        base_project = store.project_dir(pid)
        candidates = [base_project, store.RUNTIME / "jobs" / job_id, store.ROOT, HYPERFRAMES_ROOT]
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (base_project / path).resolve()
        else:
            path = path.resolve()
        if not any(_is_under(path, root) for root in candidates):
            raise RuntimeError(f"读取路径越界：{path}")
        return path

    def _resolve_write_path(self, job_id: str, raw: str) -> Path:
        pid = store.snapshot()["jobs"][job_id]["project_id"]
        base_project = store.project_dir(pid)
        base_job = store.RUNTIME / "jobs" / job_id
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (base_project / path).resolve()
        else:
            path = path.resolve()
        if not any(_is_under(path, root) for root in (base_project, base_job)):
            raise RuntimeError(f"写入路径越界：{path}")
        return path

    def _resolve_command_cwd(self, job_id: str, cwd: str) -> Path:
        if not cwd:
            return store.project_dir(store.snapshot()["jobs"][job_id]["project_id"])
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            path = (store.project_dir(store.snapshot()["jobs"][job_id]["project_id"]) / path).resolve()
        else:
            path = path.resolve()
        allowed = [
            store.project_dir(store.snapshot()["jobs"][job_id]["project_id"]),
            store.RUNTIME / "jobs" / job_id,
            store.ROOT,
            HYPERFRAMES_ROOT,
        ]
        if not any(_is_under(path, root) for root in allowed):
            raise RuntimeError(f"命令 cwd 越界：{path}")
        return path

    def _hyperframes_cli_command(self) -> str:
        if LOCAL_HYPERFRAMES_CLI.is_file():
            return shlex.quote(str(LOCAL_HYPERFRAMES_CLI))
        return "npx --yes hyperframes@0.7.41"

    def _system_prompt(self, project: dict[str, Any], prepared: PreparedSource) -> str:
        language = "简体中文" if str(project.get("output_language") or "zh").startswith("zh") else "the requested language"
        return f"""
你是 FrameCraft 单项目视频 Agent。你从头到尾负责当前项目：理解输入、设计 HyperFrames HTML、真实渲染 MP4、验收并与用户沟通。

当前项目是一键科普视频：画面主体是解释科学概念的动态图形，不使用人物口播画面。

硬性要求：
1. 只能做真实 HyperFrames 渲染，绝不允许伪装完成，绝不允许 FFmpeg 拼 PPT 或静态图集兜底。
2. 面向观众的视频里，所有屏幕文字都必须是 {language}。不要出现面向制作的术语、提示词、占位词或工作流文案。
3. 如果是中文视频，字幕必须固定居中放在底部安全区，不能偏左或偏右。
4. 画面要像高级科学动态图解：每个关键元素都有出场、退场和语义自运动；优先用机制、尺度、对比、时间线、系统关系解释旁白，不做静态幻灯片或纯文字堆叠。
5. 卡片、信息块、浮层尽量用圆角和半透明表面，避免生硬纯色底板；布局要有主次、留白和节奏。
6. 如果任务受阻，必须如实说明，并用 write_chat 告诉用户当前问题；需要用户补充信息时，用 report_progress(status=\"needs_input\") + write_chat。不要假装已经生成。
7. 必须读取项目的 `docs/MULTI_AGENT_ANIMATION_REQUIREMENTS.md` 和 `docs/SCIENCE_VIDEO_WORKFLOW.md`；前者是不可退让的最高约束，也可以读取 HyperFrames 仓库作为实现参考。
8. 不要直接运行 `hyperframes init`。创建版本目录后，优先调用 `bootstrap_hyperframes_project` 离线生成 `hyperframes/` 子工程，再在其中写 HTML 和真实渲染。

工作边界：
- 主题模式：DeepSeek 已生成科普讲稿、章节、视觉主张与来源台账；沿用这些内容继续设计。
- 文案模式：阿里云已经严格按用户原文生成旁白；不得改写正文或二次换旁白。
- 媒体模式：必须保留上传音频或视频原音轨；阿里云 ASR 只负责转写，不允许重配旁白。
- 当前工程不导出剪映草稿。

推荐流程：
- analyze 任务：先写 analysis/analysis.json 与 analysis/edit_plan.json，不渲染。
- render 任务：创建版本目录 -> bootstrap_hyperframes_project -> 准备 audio_meta / narrator_scripts / captions / scene html 或直接主 index.html -> render_hyperframes_project 真实渲染 -> 写时间线与视觉验收 JSON -> register_version。

工程建议：
- 科普视频可以做成一个强主控的 `hyperframes/index.html`，内部包含多个科学语义场景、字幕轨和音频轨；不必为了形式强拆很多文件。
- 先确保内容完整和可渲染，再追求更复杂的结构复用。
- 字体要么使用 HyperFrames 能自动解析的常见 Web 字体，要么显式写 `@font-face` 或 `local()`；如果渲染日志里出现 `font_family_without_font_face` 之类 `✗` 级问题，必须修复后重渲染。
- 字幕行要控制最大宽度，避免 `caption_text_overflow_risk`。

必须产出的文件：
- 分析阶段：analysis/analysis.json, analysis/edit_plan.json
- 渲染阶段：<version_dir>/timeline.json, <version_dir>/agent_visual_review.json, <version_dir>/preview.mp4, <version_dir>/hyperframes/

你只负责当前项目，不要访问无关目录，不要执行与项目无关的系统操作。
""".strip()

    def _user_prompt(self, job: dict[str, Any], project: dict[str, Any], prepared: PreparedSource) -> str:
        chat_note = ""
        if job["type"] == "chat":
            chat_note = f"\n用户刚刚在聊天中提出的最新要求：{job.get('payload', {}).get('message', '')}\n"
            if not bool(job.get("payload", {}).get("apply", True)):
                chat_note += (
                    "这是纯聊天模式：不要新建版本，不要渲染，不要改文件。"
                    "如有必要先 read_state，然后必须用 write_chat 直接回复用户。\n"
                )
        mode_note = (
            "当前是上传媒体模式。请保持原音频完整，按 scene_seed 的真实时间切分科学场景。"
            if prepared.mode == "media"
            else "当前是主题或文案模式。后端已经用阿里云 TTS 准备旁白并建立时间轴；基于 scene_seed 完成科学动画、字幕与成片。"
        )
        render_note = (
            "当前默认由用户电脑渲染：完成 HyperFrames HTML 工程、字幕和时间线后，必须调用 register_local_render；"
            "不要调用 render_hyperframes_project 或 register_version。"
            if self._render_target(job) == "local"
            else "当前允许服务器执行 HyperFrames 严格渲染并注册完整版本。"
        )
        return f"""
项目名：{project.get('name')}
任务类型：{job['type']}
画面比例：{project.get('aspect_ratio')}
目标时长：{project.get('target_duration')} 秒
目标风格：{project.get('target_style')}
输出语言：{project.get('output_language')}
{mode_note}
{render_note}

关键文件：
- prepared source metadata: {prepared.metadata_path}
- scene seed: {prepared.scene_seed_path}
- project output dir: {store.project_dir(project.get('id'))}
- HyperFrames repo: {HYPERFRAMES_ROOT}
- science workflow: {SCIENCE_WORKFLOW_DOC}
- uploaded-audio materializer: {store.ROOT / 'backend' / 'app' / 'materialize_audio_from_seed.py'}
{chat_note}
如果你在开始阶段还没掌握项目全貌，先调用 read_state。请自己规划工具调用顺序，并持续向用户同步关键进度。
""".strip()

    def _validate_outputs(self, job_id: str) -> bool:
        snapshot = store.snapshot()
        job = snapshot["jobs"][job_id]
        pid = job["project_id"]
        if job["status"] == "needs_input":
            return False
        if job["type"] == "analyze":
            plan = store.project_dir(pid) / "analysis" / "edit_plan.json"
            analysis = store.project_dir(pid) / "analysis" / "analysis.json"
            if not plan.is_file() or not analysis.is_file():
                self._fail(job_id, "分析任务没有生成 analysis.json 和 edit_plan.json。")
                return False
            return True
        if job["type"] in {"generate", "apply_patch", "fine_tune"}:
            local_result = job.get("result") or {}
            if local_result.get("render_target") == "local":
                version = snapshot["versions"].get(str(local_result.get("version_id") or ""))
                if not version:
                    self._fail(job_id, "未注册待本地渲染版本。")
                    return False
                version_dir = Path(version["version_dir"])
                required = [
                    version_dir / "hyperframes_project.zip",
                    version_dir / "timeline.json",
                    version_dir / "hyperframes" / "index.html",
                ]
                missing = [str(path) for path in required if not path.exists()]
                if missing:
                    self._fail(job_id, "本地渲染包缺少关键产物：\n" + "\n".join(missing))
                    return False
                return True
            version_id = (snapshot["projects"].get(pid) or {}).get("current_version_id")
            version = snapshot["versions"].get(str(version_id or ""))
            if not version:
                self._fail(job_id, "未注册任何版本。")
                return False
            version_dir = Path(version["version_dir"])
            required = [
                version_dir / "preview.mp4",
                version_dir / "timeline.json",
                version_dir / "agent_visual_review.json",
                version_dir / "hyperframes" / "index.html",
            ]
            missing = [str(path) for path in required if not path.exists()]
            if missing:
                self._fail(job_id, "版本缺少关键产物：\n" + "\n".join(missing))
                return False
            return True
        if job["type"] == "chat":
            return True
        return True

    def _mark_running(self, job_id: str) -> None:
        def op(data):
            job = data["jobs"][job_id]
            job["status"] = "running"
            job["started_at"] = store.now_iso()
            job["updated_at"] = store.now_iso()
            return job

        store.mutate(op)

    def _set_step(self, job_id: str, progress: float, step: str) -> None:
        def op(data):
            job = data["jobs"][job_id]
            job["progress"] = progress
            job["current_step"] = step
            job["updated_at"] = store.now_iso()
            job.setdefault("logs", []).append(step)
            return job

        store.mutate(op)

    def _append_log(self, job_id: str, message: str, chat: bool = False) -> None:
        pid = store.snapshot()["jobs"][job_id]["project_id"]

        def op(data):
            job = data["jobs"].get(job_id)
            if not job:
                return None
            job.setdefault("logs", []).append(message)
            job["updated_at"] = store.now_iso()
            if chat:
                data.setdefault("chat", {}).setdefault(pid, []).append(
                    {
                        "id": store.new_id("msg"),
                        "project_id": pid,
                        "job_id": job_id,
                        "role": "agent",
                        "content": message,
                        "status": "log",
                        "created_at": store.now_iso(),
                    }
                )
            return job

        store.mutate(op)

    def _complete(self, job_id: str) -> None:
        def op(data):
            job = data["jobs"][job_id]
            if job["status"] != "needs_input":
                job["status"] = "completed"
            job["progress"] = max(float(job.get("progress") or 0), 100.0)
            local_pending = (job.get("result") or {}).get("render_target") == "local"
            job["current_step"] = "等待当前浏览器本地渲染" if local_pending else "完成"
            job["completed_at"] = store.now_iso()
            project = data["projects"].get(job["project_id"])
            if project and job["status"] != "needs_input":
                project["status"] = (
                    "awaiting_local_render"
                    if local_pending
                    else "completed" if job["type"] != "analyze" else "planning"
                )
                project["updated_at"] = store.now_iso()
            return job

        store.mutate(op)

    @staticmethod
    def _render_target(job: dict[str, Any]) -> str:
        # The server prepares reproducible projects only; rendering belongs to the user's device.
        return "local"

    def _needs_input(self, job_id: str, message: str) -> None:
        pid = store.snapshot()["jobs"][job_id]["project_id"]

        def op(data):
            job = data["jobs"][job_id]
            job["status"] = "needs_input"
            job["error_message"] = message
            job["current_step"] = message
            job["completed_at"] = store.now_iso()
            data.setdefault("chat", {}).setdefault(pid, []).append(
                {
                    "id": store.new_id("msg"),
                    "project_id": pid,
                    "job_id": job_id,
                    "role": "agent",
                    "content": message,
                    "status": "needs_input",
                    "created_at": store.now_iso(),
                }
            )
            project = data["projects"].get(pid)
            if project:
                project["status"] = "needs_input"
                project["updated_at"] = store.now_iso()
            return job

        store.mutate(op)

    def _fail(self, job_id: str, message: str) -> None:
        def op(data):
            job = data["jobs"].get(job_id)
            if not job:
                return None
            job["status"] = "failed"
            job["error_message"] = message
            job["completed_at"] = store.now_iso()
            job["updated_at"] = store.now_iso()
            data.setdefault("chat", {}).setdefault(job["project_id"], []).append(
                {
                    "id": store.new_id("msg"),
                    "project_id": job["project_id"],
                    "job_id": job_id,
                    "role": "agent",
                    "content": f"当前版本尚未达到交付标准，Agent 已停止登记成片。具体原因：{message}",
                    "status": "failed",
                    "created_at": store.now_iso(),
                }
            )
            project = data["projects"].get(job["project_id"])
            if project:
                project["status"] = "failed"
                project["updated_at"] = store.now_iso()
            return job

        store.mutate(op)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]..."


def _normalize_tool_arguments(raw: Any) -> str:
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    text = str(raw or "").strip()
    if not text:
        return "{}"
    text = _strip_code_fence(text)
    for parser in (json.loads, json5.loads):
        try:
            parsed = parser(text)
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            continue
    return "{}"


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text


def _managed_dimensions(aspect_ratio: str) -> tuple[int, int]:
    ratio = (aspect_ratio or "9:16").strip()
    if ratio == "16:9":
        return 1920, 1080
    if ratio == "1:1":
        return 1080, 1080
    return 1080, 1920


def _safe_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


runner = SingleAgentRunner()
