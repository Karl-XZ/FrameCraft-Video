from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from .deepseek_api import create_client, deepseek_settings, parse_json_object
from .ingest import build_subtitle_cues, normalize_words


PROMPTS = Path(__file__).parent / "prompts"


def _text_model() -> str:
    settings = deepseek_settings()
    return settings.get("text_model") or settings.get("pro_model") or "deepseek-v4-flash"


def _call(system: str, payload: dict[str, Any], tokens: int) -> tuple[dict[str, Any] | str, dict[str, Any]]:
    started = time.perf_counter()
    model = _text_model()
    response = create_client().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        temperature=0.32,
        max_tokens=tokens,
        extra_body={"thinking": {"type": "disabled"}},
    )
    raw = (response.choices[0].message.content or "").strip()
    return raw, {"model": model, "elapsed_ms": round((time.perf_counter() - started) * 1000)}


def _dimensions(ratio: str) -> tuple[int, int]:
    return (1920, 1080) if ratio == "16:9" else ((1080, 1080) if ratio == "1:1" else (1080, 1920))


def create_plan(
    project: dict[str, Any],
    prepared: Any,
    revision_feedback: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    seed = json.loads(Path(prepared.scene_seed_path).read_text(encoding="utf-8"))
    width, height = _dimensions(str(project.get("aspect_ratio") or "9:16"))
    segments = [
        {
            "page_id": f"page-{index:02d}", "duration_s": float(scene.get("duration_s") or 0),
            "start_s": float(scene.get("start_s") or 0), "end_s": float(scene.get("end_s") or 0),
            "transcript": str(scene.get("transcript") or ""), "visual_claim": str(scene.get("visual_claim") or ""),
        }
        for index, scene in enumerate(seed.get("scenes") or [], start=1)
    ]
    if not segments:
        raise RuntimeError("没有可用于生成页面的旁白分段。")
    duration = round(sum(item["duration_s"] for item in segments), 3)
    payload = {
        "topic": project.get("topic") or project.get("name"), "requirements": project.get("requirements") or "",
        "transcript": prepared.source_text, "page_count": len(segments), "total_duration_s": duration,
        "language": "简体中文", "width": width, "height": height, "source_segments": segments,
    }
    if revision_feedback:
        payload["revision_feedback"] = revision_feedback
    raw, trace = _call((PROMPTS / "prompt_ai_system.md").read_text(encoding="utf-8"), payload, 9000)
    plan = parse_json_object(raw)
    pages = plan.get("pages") or []
    if len(pages) != len(segments):
        raise RuntimeError("提示词 AI 输出页数与旁白分段不一致。")
    for page, segment in zip(pages, segments):
        if page.get("page_id") != segment["page_id"] or abs(float(page.get("duration_s") or 0) - segment["duration_s"]) > 0.15:
            raise RuntimeError("提示词 AI 改写了页面时间轴。")
        page.pop("source_label", None)
    plan["project"] = {**plan.get("project", {}), "width": width, "height": height, "total_duration_s": duration, "language": "简体中文"}
    return plan, {"stage": "prompt_ai", "input": payload, "raw": raw, **trace}


def _caption_template(cues: list[dict[str, Any]], width: int, height: int, duration: float) -> str:
    items = "".join(f'<div id="cap-{i}" class="cap">{_escape(str(c["text"]))}</div>' for i, c in enumerate(cues))
    actions = []
    for i, cue in enumerate(cues):
        start, end = float(cue["start"]), float(cue["end"])
        actions.append(f"tl.to(q('#cap-{i}'),{{autoAlpha:1,y:0,duration:.16,ease:'power2.out'}},{start:.3f});tl.to(q('#cap-{i}'),{{autoAlpha:0,y:-8,duration:.16,ease:'power2.in'}},{max(start+.3,end-.12):.3f});")
    return f'''<template id="captions-template"><div class="clip" data-composition-id="captions" data-width="{width}" data-height="{height}" data-start="0" data-duration="{duration:.3f}"><style>@font-face{{font-family:'FrameCraftCN';src:local('PingFang SC'),local('Microsoft YaHei'),local('Noto Sans CJK SC');font-weight:100 900}}[data-composition-id="captions"]{{position:absolute;inset:0;z-index:2147483647;isolation:isolate;pointer-events:none;font-family:FrameCraftCN}}.cap{{position:absolute;z-index:2147483647;left:50%;bottom:{70 if height>width else 56}px;transform:translateX(-50%);max-width:{int(width*.78)}px;padding:12px 24px;border-radius:18px;background:rgba(0,0,0,.52);color:#fff;font-size:{42 if height>width else 32}px;line-height:1.35;text-align:center;opacity:0;white-space:nowrap;filter:drop-shadow(0 12px 28px rgba(0,0,0,.32))}}</style>{items}<script>const root=document.querySelector('[data-composition-id="captions"]');const q=s=>root.querySelector(s);const tl=gsap.timeline({{paused:true}});{''.join(actions)}window.__timelines=window.__timelines||{{}};window.__timelines['captions']=tl;</script></div></template>'''


def _host(plan: dict[str, Any], audio: str, cues: list[dict[str, Any]]) -> str:
    project, pages = plan["project"], plan["pages"]
    width, height, total = project["width"], project["height"], project["total_duration_s"]
    cursor, mounts = 0.0, []
    for index, page in enumerate(pages, start=1):
        dur = float(page["duration_s"])
        mounts.append(f'<div id="slot-{index}" data-composition-id="{page["page_id"]}" data-composition-src="compositions/{page["page_id"]}.html" data-start="{cursor:.3f}" data-duration="{dur:.3f}" data-track-index="1" data-width="{width}" data-height="{height}"></div>')
        cursor += dur
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width={width}, height={height}"><script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script><style>*{{box-sizing:border-box}}html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;background:#050810}}#main{{position:relative;isolation:isolate;width:{width}px;height:{height}px}}</style></head><body><div id="main" data-composition-id="main" data-width="{width}" data-height="{height}" data-start="0" data-duration="{total}">{''.join(mounts)}<div data-composition-id="captions" data-composition-src="compositions/captions.html" data-start="0" data-duration="{total}" data-track-index="9999" data-width="{width}" data-height="{height}"></div><audio id="narration" data-start="0" data-duration="{total}" data-track-index="0" src="assets/{audio}" preload="auto"></audio></div><script>window.__timelines=window.__timelines||{{}};window.__timelines.main=gsap.timeline({{paused:true}});</script></body></html>'''


def materialize_two_stage_version(
    project: dict[str, Any],
    prepared: Any,
    version_dir: Path,
    hyperframes_dir: Path,
    audio_asset_name: str,
    progress: Callable[[int, str], None] | None = None,
    revision_feedback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan, plan_trace = create_plan(project, prepared, revision_feedback=revision_feedback)
    words = normalize_words(json.loads(Path(prepared.transcript_path).read_text(encoding="utf-8")))
    cues = build_subtitle_cues(words)
    comp_dir = hyperframes_dir / "compositions"; comp_dir.mkdir(parents=True, exist_ok=True)
    page_traces = []
    system = (PROMPTS / "animation_ai_system.md").read_text(encoding="utf-8")
    for index, page in enumerate(plan["pages"], start=1):
        if progress: progress(48 + int(index * 22 / len(plan["pages"])), f"动画 AI 正在生成第 {index}/{len(plan['pages'])} 页")
        raw, trace = _call(system, {"project": plan["project"], "page": page}, 14000)
        raw = re.sub(r"^```(?:html)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
        pid = str(page["page_id"])
        if f'<template id="{pid}-template">' not in raw or f'data-composition-id="{pid}"' not in raw or f"window.__timelines['{pid}']" not in raw or "<!doctype" in raw.lower():
            raise RuntimeError(f"动画 AI 返回的第 {index} 页不符合 HyperFrames 子 composition 契约。")
        (comp_dir / f"{pid}.html").write_text(raw + "\n", encoding="utf-8")
        page_traces.append({"page_id": pid, "raw": raw, **trace})
    (comp_dir / "captions.html").write_text(_caption_template(cues, int(plan["project"]["width"]), int(plan["project"]["height"]), float(plan["project"]["total_duration_s"])), encoding="utf-8")
    (hyperframes_dir / "index.html").write_text(_host(plan, audio_asset_name, cues), encoding="utf-8")
    timeline = {"project_id": project.get("id"), "total_duration": plan["project"]["total_duration_s"], "scenes":[{"scene_number":i,"scene_id":p["page_id"],"start_time":round(sum(float(x["duration_s"]) for x in plan["pages"][:i-1]),3),"end_time":round(sum(float(x["duration_s"]) for x in plan["pages"][:i]),3),"duration":p["duration_s"],"headline":p.get("title"),"semantic_motion":p.get("visual_concept")} for i,p in enumerate(plan["pages"],1)],"captions":cues}
    (version_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (version_dir / "two_stage_trace.json").write_text(json.dumps({"prompt_ai":plan_trace,"animation_ai":page_traces},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return {"duration_s":float(plan["project"]["total_duration_s"]),"scene_count":len(plan["pages"]),"caption_count":len(cues),"scene_code_count":len(plan["pages"]),"plan":plan}


def materialize_two_stage_scene_repair(
    project: dict[str, Any],
    prepared: Any,
    base_version_dir: Path,
    version_dir: Path,
    hyperframes_dir: Path,
    audio_asset_name: str,
    scene_number: int,
    repair_feedback: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    trace_path = base_version_dir / "two_stage_trace.json"
    if not trace_path.is_file():
        raise RuntimeError("基准版本缺少 two_stage_trace.json，无法只重画单幕。")
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    plan = parse_json_object((trace.get("prompt_ai") or {}).get("raw") or "{}")
    pages = plan.get("pages") or []
    if scene_number < 1 or scene_number > len(pages):
        raise RuntimeError(f"单幕修复目标超出范围：第 {scene_number} 幕。")
    width, height = _dimensions(str(project.get("aspect_ratio") or "9:16"))
    duration = round(sum(float(item.get("duration_s") or 0) for item in pages), 3)
    plan["project"] = {
        **(plan.get("project") or {}),
        "width": width,
        "height": height,
        "total_duration_s": duration,
        "language": "简体中文",
    }
    for page in pages:
        page.pop("source_label", None)

    target_page = dict(pages[scene_number - 1])
    system = (PROMPTS / "animation_ai_system.md").read_text(encoding="utf-8") + """

现在执行单幕修复：只重画输入 page 对应这一幕。你必须彻底修复 repair_feedback 指出的问题，尤其是黑屏、空白、裁切、元素缺失、动画幅度不足或三帧近乎静止。其他幕不会重画，所以本幕需保持全片配色连续，但 DOM、SVG 路径、布局和动画节奏必须针对问题重新设计，不要只改文字、颜色或局部坐标。
"""
    if progress:
        progress(46, f"动画 AI 正在只重画第 {scene_number} 幕")
    raw, animation_trace = _call(
        system,
        {"project": plan["project"], "page": target_page, "repair_feedback": repair_feedback},
        14000,
    )
    raw = re.sub(r"^```(?:html)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    pid = str(target_page["page_id"])
    if f'<template id="{pid}-template">' not in raw or f'data-composition-id="{pid}"' not in raw or f"window.__timelines['{pid}']" not in raw or "<!doctype" in raw.lower():
        raise RuntimeError(f"动画 AI 返回的第 {scene_number} 幕不符合 HyperFrames 子 composition 契约。")

    comp_dir = hyperframes_dir / "compositions"
    comp_dir.mkdir(parents=True, exist_ok=True)
    (comp_dir / f"{pid}.html").write_text(raw + "\n", encoding="utf-8")
    words = normalize_words(json.loads(Path(prepared.transcript_path).read_text(encoding="utf-8")))
    cues = build_subtitle_cues(words)
    (comp_dir / "captions.html").write_text(_caption_template(cues, width, height, duration), encoding="utf-8")
    (hyperframes_dir / "index.html").write_text(_host(plan, audio_asset_name, cues), encoding="utf-8")
    timeline = {
        "project_id": project.get("id"),
        "total_duration": duration,
        "scenes": [
            {
                "scene_number": i,
                "scene_id": p["page_id"],
                "start_time": round(sum(float(x["duration_s"]) for x in pages[: i - 1]), 3),
                "end_time": round(sum(float(x["duration_s"]) for x in pages[:i]), 3),
                "duration": p["duration_s"],
                "headline": p.get("title"),
                "semantic_motion": p.get("visual_concept"),
            }
            for i, p in enumerate(pages, 1)
        ],
        "captions": cues,
    }
    (version_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    trace["single_scene_repair"] = {
        "scene_number": scene_number,
        "page_id": pid,
        "repair_feedback": repair_feedback,
        "animation_ai": {"page_id": pid, "raw": raw, **animation_trace},
    }
    (version_dir / "two_stage_trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "duration_s": duration,
        "scene_count": len(pages),
        "caption_count": len(cues),
        "scene_code_count": 1,
        "plan": plan,
        "repaired_scene_number": scene_number,
    }


def build_two_stage_analysis(project: dict[str, Any], prepared: Any) -> dict[str, Any]:
    plan, trace = create_plan(project, prepared)
    words = normalize_words(json.loads(Path(prepared.transcript_path).read_text(encoding="utf-8")))
    cues = build_subtitle_cues(words)
    return {"analysis":{"project_id":project.get("id"),"summary":"提示词 AI 已生成逐页科学动画设计。","scene_count":len(plan["pages"]),"total_duration_s":plan["project"]["total_duration_s"],"scenes":plan["pages"],"captions":cues},"edit_plan":{"video_concept":plan["project"].get("topic"),"target_duration":plan["project"]["total_duration_s"],"subtitle_style":"简体中文字幕固定居中放在底部安全区并淡入淡出。","scenes":plan["pages"]},"plan":plan,"trace":trace,"scene_count":len(plan["pages"]),"caption_count":len(cues),"duration_s":plan["project"]["total_duration_s"]}


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
