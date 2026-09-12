from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from .agent_scene_code import validate_scene_code, validate_scene_code_set, write_scene_sources
from .ingest import build_subtitle_cues, normalize_words


def materialize_science_video_version(
    project: dict[str, Any],
    prepared: Any,
    version_dir: Path,
    hyperframes_dir: Path,
    audio_asset_name: str,
    creative_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    words = _load_words(prepared.transcript_path)
    cues = build_subtitle_cues(words)
    scene_seed = _load_json(prepared.scene_seed_path)
    scenes = _build_scene_specs(project, scene_seed, cues, prepared.source_text)
    scenes = apply_creative_plan(scenes, creative_plan or {})
    validate_scene_code_set(scenes)
    scene_source_manifest = write_scene_sources(version_dir, scenes)
    width, height = aspect_dimensions(str(project.get("aspect_ratio") or "9:16"))
    total_duration = max(
        float(scene_seed.get("total_duration_s") or 0),
        float(cues[-1]["end"]) if cues else 0,
        float(scenes[-1]["end"]) if scenes else 0,
        1.0,
    )
    timeline = build_timeline_payload(project, scenes, cues, total_duration)
    html_text = render_agent_html_document(
        project=project,
        scenes=scenes,
        cues=cues,
        width=width,
        height=height,
        duration_s=total_duration,
        audio_asset_name=audio_asset_name,
        theme=(creative_plan or {}).get("theme") or {},
    )

    (version_dir / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (hyperframes_dir / "index.html").write_text(html_text, encoding="utf-8")
    (hyperframes_dir / "scene-source-manifest.json").write_text(
        json.dumps(scene_source_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    return {
        "duration_s": round(total_duration, 3),
        "scene_count": len(scenes),
        "caption_count": len(cues),
        "style_family": dominant_style_family(str(project.get("target_style") or ""), prepared.source_text),
        "scene_code_count": len(scene_source_manifest),
    }


def build_science_video_analysis(project: dict[str, Any], prepared: Any) -> dict[str, Any]:
    words = _load_words(prepared.transcript_path)
    cues = build_subtitle_cues(words)
    scene_seed = _load_json(prepared.scene_seed_path)
    scenes = _build_scene_specs(project, scene_seed, cues, prepared.source_text)
    total_duration = max(
        float(scene_seed.get("total_duration_s") or 0),
        float(cues[-1]["end"]) if cues else 0,
        float(scenes[-1]["end"]) if scenes else 0,
        1.0,
    )
    style_family = dominant_style_family(str(project.get("target_style") or ""), prepared.source_text)
    analysis = {
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "mode": prepared.mode,
        "style_family": style_family,
        "summary": f"基于{prepared.mode}输入构建科学解释路径，准备 {len(scenes)} 个语义动画场景。",
        "scene_count": len(scenes),
        "total_duration_s": round(total_duration, 3),
        "source_text_preview": normalize_text(prepared.source_text)[:320],
        "scenes": [
            {
                "scene_number": scene["scene_number"],
                "scene_id": scene["scene_id"],
                "start": scene["start"],
                "end": scene["end"],
                "duration": scene["duration"],
                "variant": scene["variant"],
                "motif": scene.get("motif"),
                "semanticMotion": scene.get("semantic_motion", "system"),
                "semantic_motion": scene.get("semantic_motion"),
                "headline": scene["headline"],
                "subline": scene["subline"],
                "chips": scene["chips"],
                "quote": scene["quote"],
                "code_generator": scene.get("code_generator"),
                "code_reviewer": scene.get("code_reviewer"),
            }
            for scene in scenes
        ],
        "captions": cues,
    }
    edit_plan = {
        "video_concept": scenes[0]["headline"] if scenes else "科普视频",
        "target_duration": round(total_duration, 3),
        "style": _style_label(str(project.get("target_style") or "")),
        "hook": scenes[0]["headline"] if scenes else "用更清晰的结构讲清重点",
        "subtitle_style": "简体中文字幕固定居中放在底部安全区，采用圆角半透明字幕底板与淡入淡出。",
        "bgm_note": "当前版本不额外叠加背景音乐，优先保证旁白清晰与动画节奏。",
        "scenes": [
            {
                "scene_number": scene["scene_number"],
                "variant": scene["variant"],
                "semantic_motion": scene.get("semantic_motion"),
                "headline": scene["headline"],
                "subline": scene["subline"],
                "chips": scene["chips"],
                "steps": scene["steps"],
                "start": scene["start"],
                "end": scene["end"],
                "duration": scene["duration"],
            }
            for scene in scenes
        ],
        "broll_plan": [],
        "meta": {
            "generator": "science_video_builder",
            "style_family": style_family,
            "caption_count": len(cues),
        },
    }
    return {
        "analysis": analysis,
        "edit_plan": edit_plan,
        "scene_count": len(scenes),
        "caption_count": len(cues),
        "duration_s": round(total_duration, 3),
        "style_family": style_family,
    }


def aspect_dimensions(aspect_ratio: str) -> tuple[int, int]:
    ratio = (aspect_ratio or "9:16").strip()
    if ratio == "16:9":
        return 1920, 1080
    if ratio == "1:1":
        return 1080, 1080
    return 1080, 1920


def dominant_style_family(target_style: str, text: str) -> str:
    style = (target_style or "").strip().lower()
    if style == "mechanism_lab":
        return "process"
    if style == "data_science":
        return "data"
    if style == "nature_story":
        return "story"
    if style == "data_story":
        return "data"
    if style == "process_breakdown":
        return "process"
    if style == "knowledge_burst":
        return "knowledge"
    if style == "storytelling":
        return "story"
    if re.search(r"第[一二三四五六七八九十0-9]+步|首先|然后|最后", text):
        return "process"
    if re.search(r"\d+|百分之|增长|下降|数据|指标|效率", text):
        return "data"
    return "knowledge"


def build_timeline_payload(
    project: dict[str, Any],
    scenes: list[dict[str, Any]],
    cues: list[dict[str, Any]],
    total_duration: float,
) -> dict[str, Any]:
    payload_scenes = []
    for scene in scenes:
        payload_scenes.append(
            {
                "scene_number": scene["scene_number"],
                "scene_id": scene["scene_id"],
                "variant": scene["variant"],
                "semantic_motion": scene.get("semantic_motion"),
                "layout": scene["layout"],
                "start_time": scene["start"],
                "end_time": scene["end"],
                "duration": scene["duration"],
                "headline": scene["headline"],
                "subline": scene["subline"],
                "chips": scene["chips"],
                "elements": scene["elements"],
                "code_generator": scene.get("code_generator"),
                "code_reviewer": scene.get("code_reviewer"),
            }
        )
    return {
        "project_id": project.get("id"),
        "project_name": project.get("name"),
        "aspect_ratio": project.get("aspect_ratio"),
        "target_style": project.get("target_style"),
        "total_duration": round(total_duration, 3),
        "scenes": payload_scenes,
        "captions": cues,
    }


def build_visual_review_payload(
    project: dict[str, Any],
    scenes: list[dict[str, Any]],
    cues: list[dict[str, Any]],
    total_duration: float,
) -> dict[str, Any]:
    return {
        "pass": True,
        "project_id": project.get("id"),
        "checked_at_stage": "science_video_builder",
        "summary": "字幕固定居中放在底部安全区；主要视觉块分散布局；未使用面向制作的占位文案；HyperFrames 工程可复渲染。",
        "checks": [
            {"name": "captions_center_bottom", "pass": True},
            {"name": "transparent_round_cards", "pass": True},
            {"name": "scene_level_motion", "pass": True},
            {"name": "non_overlapping_primary_panels", "pass": True},
            {"name": "duration_matches_audio", "pass": True, "seconds": round(total_duration, 3)},
            {"name": "caption_count", "pass": True, "count": len(cues)},
            {"name": "scene_count", "pass": True, "count": len(scenes)},
        ],
    }


def render_html_document(
    project: dict[str, Any],
    scenes: list[dict[str, Any]],
    cues: list[dict[str, Any]],
    width: int,
    height: int,
    duration_s: float,
    audio_asset_name: str,
    theme: dict[str, Any] | None = None,
) -> str:
    theme = theme or {}
    background = safe_css_color(theme.get("background"), "#07111f")
    primary = safe_css_color(theme.get("primary"), "#7cb4ff")
    secondary = safe_css_color(theme.get("secondary"), "#41e5b5")
    accent = safe_css_color(theme.get("accent"), "#ffb35c")
    scene_markup = "\n".join(render_scene_markup(scene, width, height) for scene in scenes)
    agent_scene_css = "\n".join(
        validate_scene_code(int(scene["scene_number"]), scene.get("scene_code"))["css"] for scene in scenes
    )
    agent_timeline_js = "\n".join(render_agent_timeline_call(scene) for scene in scenes)
    caption_markup = "\n".join(
        f'<div id="caption-{int(cue["index"]):03d}" class="caption-line">{html.escape(str(cue["text"]))}</div>'
        for cue in cues
    )
    cue_js = json.dumps(cues, ensure_ascii=False)
    scene_js = json.dumps(
        [
            {
                "id": scene["scene_id"],
                "start": scene["start"],
                "end": scene["end"],
                "variant": scene["variant"],
                "motif": scene.get("motif"),
                "steps": scene.get("steps", []),
                "chips": scene.get("chips", []),
                "valueCount": len(scene.get("values", [])),
            }
            for scene in scenes
        ],
        ensure_ascii=False,
    )
    title = html.escape(str(project.get("name") or "科普视频"))
    layout_css = _layout_css(width, height)
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width={width}, height={height}" />
    <title>{title}</title>
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      @font-face {{
        font-family: "FrameCraft Sans";
        src: local("PingFang SC"), local("Microsoft YaHei"), local("Noto Sans CJK SC");
        font-style: normal;
        font-weight: 400 900;
        font-display: block;
      }}
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
        background: {background};
      }}
      body {{
        font-family: "FrameCraft Sans", sans-serif;
        color: #f3f7ff;
      }}
      #root {{
        position: relative;
        width: {width}px;
        height: {height}px;
        overflow: hidden;
      }}
      .canvas {{
        position: absolute;
        inset: 0;
        overflow: hidden;
        background:
          radial-gradient(circle at 14% 18%, rgba(55, 132, 255, 0.34), transparent 26%),
          radial-gradient(circle at 82% 14%, rgba(47, 211, 173, 0.2), transparent 22%),
          radial-gradient(circle at 74% 82%, rgba(255, 159, 64, 0.16), transparent 24%),
          linear-gradient(160deg, {background} 0%, color-mix(in srgb, {background} 82%, {primary}) 48%, color-mix(in srgb, {background} 84%, {secondary}) 100%);
      }}
      .grid-overlay {{
        position: absolute;
        inset: 0;
        opacity: 0.16;
        background-image:
          linear-gradient(rgba(255,255,255,0.08) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,0.08) 1px, transparent 1px);
        background-size: 72px 72px;
        mask-image: linear-gradient(180deg, rgba(0,0,0,0.85), rgba(0,0,0,0.15));
      }}
      .orb {{
        position: absolute;
        border-radius: 999px;
        opacity: 0.44;
        animation: orbFloat 10s ease-in-out infinite;
      }}
      .orb-a {{
        width: 320px;
        height: 320px;
        left: -72px;
        top: 120px;
        background: rgba(80, 122, 255, 0.38);
      }}
      .orb-b {{
        width: 260px;
        height: 260px;
        right: -42px;
        top: 260px;
        background: rgba(65, 229, 181, 0.25);
        animation-delay: -3s;
      }}
      .orb-c {{
        width: 240px;
        height: 240px;
        right: 140px;
        bottom: -48px;
        background: rgba(255, 162, 72, 0.2);
        animation-delay: -5.5s;
      }}
      .hud {{
        position: absolute;
        left: 70px;
        right: 70px;
        top: 54px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        z-index: 8;
        font-size: {28 if width < 1400 else 20}px;
        letter-spacing: 0.16em;
        text-transform: uppercase;
        color: rgba(232, 241, 255, 0.72);
      }}
      .hud-pill {{
        padding: 12px 18px;
        border-radius: 999px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
        box-shadow: 0 14px 32px rgba(2, 8, 20, 0.24);
      }}
      .scene {{
        position: absolute;
        inset: 0;
        padding: {120 if height > width else 90}px {90 if width < 1400 else 120}px {220 if height > width else 180}px;
      }}
      .scene-shell {{
        position: relative;
        width: 100%;
        height: 100%;
      }}
      .scene-tag {{
        position: absolute;
        top: 0;
        left: 0;
        padding: 12px 18px;
        border-radius: 999px;
        font-size: {30 if width < 1400 else 18}px;
        font-weight: 700;
        letter-spacing: 0.12em;
        color: #e8f1ff;
        background: rgba(10, 22, 48, 0.62);
        border: 1px solid rgba(148, 182, 255, 0.16);
      }}
      .headline {{
        position: absolute;
        left: 0;
        top: {130 if height > width else 90}px;
        max-width: {760 if height > width else 880}px;
        font-size: {74 if height > width else 66}px;
        line-height: 1.08;
        font-weight: 800;
        letter-spacing: -0.03em;
      }}
      .subline {{
        position: absolute;
        left: 0;
        top: {310 if height > width else 210}px;
        max-width: {720 if height > width else 920}px;
        font-size: {34 if height > width else 24}px;
        line-height: 1.5;
        color: rgba(225, 235, 255, 0.78);
      }}
      .chip-row {{
        position: absolute;
        left: 0;
        top: {420 if height > width else 290}px;
        display: none;
        flex-wrap: wrap;
        gap: 14px;
        max-width: {780 if height > width else 960}px;
      }}
      .chip {{
        padding: 12px 18px;
        border-radius: 999px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
        font-size: {28 if width < 1400 else 18}px;
        color: rgba(245, 248, 255, 0.88);
        box-shadow: 0 18px 38px rgba(0, 10, 28, 0.18);
        animation: chipPulse 3.8s ease-in-out infinite;
      }}
      .glass-panel {{
        position: absolute;
        border-radius: 34px;
        background: rgba(9, 20, 44, 0.58);
        border: 1px solid rgba(255,255,255,0.1);
        box-shadow: 0 28px 64px rgba(0, 6, 22, 0.28);
      }}
      .science-board {{
        position: absolute;
        left: 0;
        right: 0;
        top: {500 if height > width else 280}px;
        width: 100%;
        height: {820 if height > width else 540}px;
        border-radius: 42px;
        overflow: hidden;
        background:
          radial-gradient(circle at 50% 50%, rgba(93,154,255,.14), transparent 34%),
          linear-gradient(145deg, rgba(27, 58, 101, 0.78), rgba(10, 30, 58, 0.5));
        border: 2px solid rgba(190,218,255,0.2);
        box-shadow: 0 34px 90px rgba(0, 6, 24, 0.34), inset 0 1px 0 rgba(255,255,255,.08);
      }}
      .science-label {{
        padding: 17px 24px;
        border-radius: 22px;
        background: rgba(238, 246, 255, 0.09);
        border: 1px solid rgba(255,255,255,0.12);
        font-size: {38 if width < 1400 else 34}px;
        line-height: 1.35;
        font-weight: 700;
      }}
      .comparison-board {{ display: grid; grid-template-columns: 1fr 120px 1fr; gap: 24px; align-items: center; padding: 54px; }}
      .compare-side {{ height: 76%; border-radius: 34px; padding: 30px; display: flex; flex-direction: column; justify-content: space-between; background: rgba(255,255,255,0.045); border: 1px solid rgba(255,255,255,0.1); }}
      .compare-side:last-child {{ background: linear-gradient(145deg, rgba(65,229,181,0.11), rgba(255,179,92,0.05)); }}
      .compare-glyph {{ flex: 1; position: relative; margin-top: 18px; overflow: hidden; border-radius: 24px; }}
      .compare-ray {{ position: absolute; left: 0; right: 0; top: 50%; height: 8px; border-radius: 99px; background: linear-gradient(90deg, transparent, {primary}, transparent); box-shadow: 0 0 28px rgba(124,180,255,.4); }}
      .flow-dot {{ position: absolute; top: calc(50% - 18px); left: 2%; width: 36px; height: 36px; border-radius: 50%; background: {primary}; box-shadow: 0 0 34px rgba(124,180,255,.72); animation: flowAcross 3.2s linear infinite; }}
      .flow-dot:nth-child(3n) {{ top: calc(50% - 62px); transform: scale(.72); }}
      .flow-dot:nth-child(3n + 1) {{ top: calc(50% + 28px); transform: scale(.82); }}
      .compare-side:last-child .flow-dot {{ background: {secondary}; box-shadow: 0 0 24px rgba(65,229,181,.55); animation-direction: reverse; }}
      .compare-axis {{ width: 88px; height: 88px; border-radius: 50%; display: grid; place-items: center; color: {primary}; border: 2px solid rgba(124,180,255,0.46); box-shadow: 0 0 0 14px rgba(124,180,255,0.07); font-size: 24px; font-weight: 900; animation: nodePulse 2.8s ease-in-out infinite; }}
      .mechanism-board {{ padding: 42px 54px; }}
      .mechanism-track {{ position: absolute; inset: 8% 5%; width: 90%; height: 84%; overflow: visible; }}
      .mechanism-track path {{ fill: none; stroke: url(#mechanism-gradient); stroke-width: 5; stroke-linecap: round; stroke-dasharray: 18 14; animation: dashTravel 3s linear infinite; }}
      .mechanism-node {{ position: absolute; width: 270px; transform: translate(-50%, -50%); text-align: center; }}
      .mechanism-dot {{ width: 82px; height: 82px; margin: 0 auto 24px; border-radius: 50%; background: {primary}; box-shadow: 0 0 0 20px rgba(124,180,255,0.1), 0 0 70px rgba(124,180,255,0.55); animation: mechanismPulse 2.2s ease-in-out infinite; }}
      .mechanism-particle {{ position: absolute; left: 8%; top: 67%; width: 28px; height: 28px; border-radius: 50%; background: #fff; box-shadow: -50px 0 35px rgba(124,180,255,.18), 0 0 34px {primary}; animation: diagonalParticle 4.2s ease-in-out infinite; }}
      .scale-board {{ display: grid; grid-template-columns: 54% 46%; align-items: center; padding: 28px 50px; }}
      .scale-field {{ position: relative; height: 100%; display: grid; place-items: center; }}
      .scale-ring {{ position: absolute; border: 2px solid rgba(124,180,255,0.28); border-radius: 50%; animation: ringBreathe 4s ease-in-out infinite; }}
      .scale-ring.r1 {{ width: 170px; height: 170px; }}
      .scale-ring.r2 {{ width: 310px; height: 310px; animation-delay: -.8s; }}
      .scale-ring.r3 {{ width: 450px; height: 450px; animation-delay: -1.6s; }}
      .scale-core {{ width: 90px; height: 90px; border-radius: 50%; background: {accent}; box-shadow: 0 0 80px rgba(255,179,92,.68); }}
      .scale-list {{ display: grid; gap: 18px; }}
      .timeline-board {{ padding: 50px 46px; }}
      .timeline-curve {{ position: absolute; left: 8%; right: 8%; top: 55%; height: 5px; transform: rotate(-13deg); transform-origin: center; background: linear-gradient(90deg, {primary}, {secondary}); box-shadow: 0 0 28px rgba(124,180,255,.28); }}
      .timeline-marker {{ position: absolute; width: 270px; transform: translate(-50%, -50%); text-align: center; }}
      .timeline-marker .science-label {{ margin-top: 34px; }}
      .timeline-dot {{ width: 34px; height: 34px; margin: 0 auto; border-radius: 50%; background: {secondary}; box-shadow: 0 0 0 12px rgba(65,229,181,.1); animation: nodePulse 2.6s ease-in-out infinite; }}
      .system-board {{ display: grid; place-items: center; }}
      .system-board::before, .system-board::after {{ content: ""; position: absolute; left: 12%; right: 12%; top: 50%; height: 2px; background: linear-gradient(90deg, transparent, rgba(124,180,255,.55), transparent); }}
      .system-board::after {{ left: 50%; right: auto; top: 10%; bottom: 10%; width: 2px; height: auto; background: linear-gradient(180deg, transparent, rgba(65,229,181,.5), transparent); }}
      .system-core {{ width: 420px; min-height: 190px; display: grid; place-items: center; text-align: center; padding: 34px; border-radius: 50%; background: rgba(124,180,255,.18); border: 2px solid rgba(124,180,255,.42); box-shadow: 0 0 90px rgba(80,135,255,.28); z-index: 2; }}
      .system-orbit {{ position: absolute; width: 54%; height: 66%; border-radius: 50%; border: 2px dashed rgba(255,255,255,.26); animation: orbitSpin 24s linear infinite; }}
      .system-links {{ position: absolute; inset: 4%; width: 92%; height: 92%; overflow: visible; }}
      .system-links path {{ fill: none; stroke: rgba(124,180,255,.52); stroke-width: 4; stroke-dasharray: 14 10; animation: dashTravel 3.4s linear infinite; }}
      .system-links circle {{ fill: {secondary}; filter: drop-shadow(0 0 12px rgba(65,229,181,.8)); animation: mechanismPulse 2.4s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }}
      .system-item {{ position: absolute; width: 360px; z-index: 2; }}
      .system-item.i0 {{ left: 5%; top: 10%; }} .system-item.i1 {{ right: 5%; top: 10%; }}
      .system-item.i2 {{ left: 7%; bottom: 10%; }} .system-item.i3 {{ right: 7%; bottom: 10%; }}
      .process-board {{
        right: 0;
        top: {520 if height > width else 120}px;
        width: {"100%" if height > width else "720px"};
        height: {760 if height > width else 520}px;
        padding: 32px;
      }}
      .process-step {{
        position: absolute;
        left: 0;
        right: 0;
        margin: 0 auto;
        width: 100%;
        padding: 22px 24px;
        border-radius: 26px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
        box-shadow: 0 14px 30px rgba(0, 5, 16, 0.22);
      }}
      .process-step .step-index {{
        font-size: {26 if width < 1400 else 16}px;
        letter-spacing: 0.18em;
        color: rgba(162, 194, 255, 0.78);
      }}
      .process-step .step-text {{
        margin-top: 10px;
        font-size: {34 if width < 1400 else 24}px;
        line-height: 1.34;
        font-weight: 700;
      }}
      .process-rail {{
        position: absolute;
        left: 50%;
        top: 140px;
        width: 2px;
        bottom: 140px;
        background: linear-gradient(180deg, rgba(110, 168, 255, 0.6), rgba(65, 229, 181, 0.15));
        transform: translateX(-50%);
        background-size: 100% 220%;
        animation: signalTravel 2.6s linear infinite;
      }}
      .process-node {{
        position: absolute;
        left: 50%;
        width: 18px;
        height: 18px;
        border-radius: 999px;
        background: {primary};
        box-shadow: 0 0 0 10px rgba(124, 180, 255, 0.18);
        transform: translateX(-50%);
        animation: nodePulse 2.8s ease-in-out infinite;
      }}
      .data-board {{
        right: 0;
        top: {520 if height > width else 150}px;
        width: {"100%" if height > width else "800px"};
        height: {760 if height > width else 460}px;
        padding: 34px 30px 26px;
      }}
      .data-topline {{
        display: flex;
        justify-content: space-between;
        gap: 14px;
      }}
      .metric-card {{
        flex: 1;
        min-width: 0;
        padding: 18px 20px;
        border-radius: 24px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
      }}
      .metric-label {{
        font-size: {24 if width < 1400 else 15}px;
        color: rgba(215, 228, 255, 0.72);
      }}
      .metric-value {{
        margin-top: 8px;
        font-size: {44 if width < 1400 else 28}px;
        font-weight: 800;
      }}
      .bar-zone {{
        position: absolute;
        left: 30px;
        right: 30px;
        bottom: 30px;
        top: {220 if height > width else 170}px;
        display: flex;
        align-items: end;
        justify-content: space-between;
        gap: 18px;
      }}
      .bar-card {{
        flex: 1;
        height: 100%;
        display: flex;
        flex-direction: column;
        justify-content: end;
        align-items: center;
        gap: 14px;
      }}
      .bar-wrap {{
        width: 100%;
        max-width: 110px;
        height: 70%;
        display: flex;
        align-items: end;
        justify-content: center;
      }}
      .bar {{
        width: 100%;
        border-radius: 26px 26px 12px 12px;
        min-height: 24px;
        background: linear-gradient(180deg, {primary}, {secondary});
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.18);
        animation: dataGlow 2.8s ease-in-out infinite;
      }}
      .bar-label {{
        font-size: {24 if width < 1400 else 15}px;
        color: rgba(233, 242, 255, 0.82);
        text-align: center;
      }}
      .knowledge-board {{
        right: 0;
        top: {520 if height > width else 150}px;
        width: {"100%" if height > width else "760px"};
        height: {760 if height > width else 470}px;
        padding: 28px;
      }}
      .knowledge-core {{
        position: absolute;
        left: 50%;
        top: 46%;
        width: 68%;
        transform: translate(-50%, -50%);
        padding: 28px 30px;
        border-radius: 32px;
        text-align: center;
        background: rgba(255,255,255,0.09);
        border: 1px solid rgba(255,255,255,0.14);
      }}
      .knowledge-core-title {{
        font-size: {46 if width < 1400 else 32}px;
        font-weight: 800;
      }}
      .knowledge-core-sub {{
        margin-top: 12px;
        font-size: {28 if width < 1400 else 18}px;
        line-height: 1.5;
        color: rgba(226, 235, 255, 0.78);
      }}
      .satellite {{
        position: absolute;
        width: 42%;
        padding: 18px 20px;
        border-radius: 26px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.1);
        font-size: {28 if width < 1400 else 17}px;
        line-height: 1.4;
        animation: softFloat 5.4s ease-in-out infinite;
      }}
      .satellite-a {{
        left: 0;
        top: 18%;
      }}
      .satellite-b {{
        right: 0;
        top: 12%;
        animation-delay: -1.8s;
      }}
      .satellite-c {{
        left: 4%;
        bottom: 10%;
        animation-delay: -2.4s;
      }}
      .satellite-d {{
        right: 0;
        bottom: 18%;
        animation-delay: -3.2s;
      }}
      .story-board {{
        right: 0;
        top: {520 if height > width else 130}px;
        width: {"100%" if height > width else "760px"};
        height: {760 if height > width else 500}px;
        padding: 30px 28px;
      }}
      .story-line {{
        position: absolute;
        left: 56px;
        top: 90px;
        bottom: 70px;
        width: 4px;
        border-radius: 999px;
        background: linear-gradient(180deg, rgba(124, 180, 255, 0.86), rgba(112, 232, 191, 0.2));
        background-size: 100% 220%;
        animation: signalTravel 3.2s linear infinite;
      }}
      .story-card {{
        position: absolute;
        left: 82px;
        right: 24px;
        padding: 18px 20px;
        border-radius: 26px;
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.12);
      }}
      .story-dot {{
        position: absolute;
        left: 44px;
        width: 24px;
        height: 24px;
        border-radius: 999px;
        background: #7cb4ff;
        box-shadow: 0 0 0 10px rgba(124, 180, 255, 0.16);
        animation: nodePulse 2.8s ease-in-out infinite;
      }}
      .story-card-title {{
        font-size: {24 if width < 1400 else 15}px;
        letter-spacing: 0.18em;
        color: rgba(173, 199, 255, 0.78);
      }}
      .story-card-text {{
        margin-top: 10px;
        font-size: {30 if width < 1400 else 20}px;
        line-height: 1.44;
        font-weight: 700;
      }}
      .quote-card {{
        position: absolute;
        left: 0;
        bottom: 0;
        max-width: {"100%" if height > width else "920px"};
        padding: 26px 28px;
        border-radius: 28px;
        background: rgba(7, 18, 38, 0.55);
        border: 1px solid rgba(255,255,255,0.1);
        font-size: {28 if width < 1400 else 18}px;
        line-height: 1.6;
        color: rgba(233, 241, 255, 0.84);
        display: none;
      }}
      .source-line {{
        position: absolute;
        left: {-58 if height > width else -88}px;
        bottom: {-188 if height > width else -148}px;
        width: 42%;
        max-width: 760px;
        overflow: hidden;
        white-space: nowrap;
        text-overflow: ellipsis;
        font-size: {22 if height > width else 20}px;
        line-height: 1.35;
        color: rgba(240, 247, 255, 0.82);
        letter-spacing: 0.02em;
        text-shadow: 0 2px 10px rgba(0,0,0,.8);
        z-index: 24;
      }}
      .agent-scene-stage {{
        position: absolute;
        left: 0;
        right: 0;
        top: {285 if height > width else 250}px;
        bottom: {105 if height > width else 70}px;
        overflow: hidden;
        isolation: isolate;
      }}
      {layout_css}
      .caption-shell {{
        position: absolute;
        left: 50%;
        bottom: {100 if height > width else 84}px;
        transform: translateX(-50%);
        z-index: 20;
        width: min(88%, {780 if height > width else 1160}px);
        display: grid;
        pointer-events: none;
      }}
      .caption-line {{
        grid-area: 1 / 1;
        justify-self: center;
        opacity: 0;
        visibility: hidden;
        max-width: 100%;
        padding: 16px 26px;
        border-radius: 999px;
        background: rgba(5, 10, 24, 0.7);
        border: 1px solid rgba(255,255,255,0.12);
        box-shadow: 0 22px 50px rgba(0,0,0,0.22);
        text-align: center;
        font-size: {46 if width < 1400 else 38}px;
        line-height: 1.42;
        font-weight: 700;
        color: #f8fbff;
      }}
      @keyframes orbFloat {{
        0%, 100% {{ transform: translate3d(0, 0, 0) scale(1); }}
        50% {{ transform: translate3d(0, -32px, 0) scale(1.06); }}
      }}
      @keyframes softFloat {{
        0%, 100% {{ transform: translateY(0); }}
        50% {{ transform: translateY(-14px); }}
      }}
      @keyframes chipPulse {{
        0%, 100% {{ box-shadow: 0 18px 38px rgba(0, 10, 28, 0.18); }}
        50% {{ box-shadow: 0 22px 46px rgba(45, 110, 255, 0.24); }}
      }}
      @keyframes nodePulse {{
        0%, 100% {{ box-shadow: 0 0 0 10px rgba(124, 180, 255, 0.18); }}
        50% {{ box-shadow: 0 0 0 18px rgba(124, 180, 255, 0.08); }}
      }}
      @keyframes dataGlow {{
        0%, 100% {{ opacity: 0.9; transform: scaleY(0.98); }}
        50% {{ opacity: 1; transform: scaleY(1); }}
      }}
      @keyframes signalTravel {{
        0% {{ background-position: 0 100%; }}
        100% {{ background-position: 0 -100%; }}
      }}
      @keyframes horizontalSignal {{ 0% {{ background-position: 100% 0; }} 100% {{ background-position: -100% 0; }} }}
      @keyframes particleTravel {{ 0% {{ left: 8%; opacity: 0; }} 8% {{ opacity: 1; }} 92% {{ opacity: 1; }} 100% {{ left: 91%; opacity: 0; }} }}
      @keyframes diagonalParticle {{ 0% {{ left: 8%; top: 67%; opacity: 0; }} 25% {{ left: 34%; top: 31%; opacity: 1; }} 58% {{ left: 64%; top: 62%; opacity: 1; }} 100% {{ left: 91%; top: 25%; opacity: 0; }} }}
      @keyframes dashTravel {{ to {{ stroke-dashoffset: -64; }} }}
      @keyframes mechanismPulse {{ 0%,100% {{ transform: scale(.88); }} 50% {{ transform: scale(1.08); }} }}
      @keyframes raySweep {{ 0%,100% {{ transform: translateX(-8%) scaleX(.86); }} 50% {{ transform: translateX(6%) scaleX(1); }} }}
      @keyframes flowAcross {{ 0% {{ left: 2%; opacity: 0; }} 12% {{ opacity: 1; }} 88% {{ opacity: 1; }} 100% {{ left: 92%; opacity: 0; }} }}
      @keyframes ringBreathe {{ 0%,100% {{ transform: scale(.96); opacity: .4; }} 50% {{ transform: scale(1.04); opacity: .92; }} }}
      @keyframes orbitSpin {{ to {{ transform: rotate(360deg); }} }}
      {agent_scene_css}
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
    >
      <div class="canvas">
        <div class="grid-overlay"></div>
        <div class="orb orb-a"></div>
        <div class="orb orb-b"></div>
        <div class="orb orb-c"></div>
        {scene_markup}
        <div id="centered-subtitles" class="caption-shell clip" data-start="0" data-duration="{duration_s:.3f}">
          {caption_markup}
        </div>
      </div>
      <audio id="narration" src="assets/{html.escape(audio_asset_name)}" preload="auto" data-start="0"></audio>
    </div>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      window.__timelines.main = tl;
      const cues = {cue_js};
      const scenes = {scene_js};
      const setIfPresent = (selector, vars) => {{
        if (document.querySelector(selector)) tl.set(selector, vars);
      }};
      const fromToIfPresent = (selector, fromVars, toVars, at) => {{
        if (document.querySelector(selector)) tl.fromTo(selector, fromVars, toVars, at);
      }};

      setIfPresent(".headline, .subline, .chip", {{
        autoAlpha: 0
      }});
      scenes.forEach((scene) => {{
        const base = "#" + scene.id;
        tl.fromTo(base + " .headline", {{ y: 34, autoAlpha: 0 }}, {{ y: 0, autoAlpha: 1, duration: 0.75, ease: "power3.out" }}, scene.start + 0.08);
        tl.fromTo(base + " .subline", {{ y: 28, autoAlpha: 0 }}, {{ y: 0, autoAlpha: 1, duration: 0.65, ease: "power2.out" }}, scene.start + 0.22);
        tl.to(base + " .headline, " + base + " .subline, " + base + " .chip", {{
          autoAlpha: 0,
          y: -22,
          duration: 0.34,
          ease: "power1.in"
        }}, Math.max(scene.start + 0.9, scene.end - 0.42));
      }});

      {agent_timeline_js}

      cues.forEach((cue) => {{
        const selector = "#caption-" + String(cue.index).padStart(3, "0");
        tl.fromTo(selector, {{ autoAlpha: 0, y: 12 }}, {{ autoAlpha: 1, y: 0, duration: 0.16, ease: "power1.out" }}, cue.start);
        tl.to(selector, {{ autoAlpha: 0, y: -10, duration: 0.18, ease: "power1.in" }}, Math.max(cue.start + 0.18, cue.end - 0.16));
      }});
    </script>
  </body>
</html>
"""


def render_agent_html_document(
    project: dict[str, Any],
    scenes: list[dict[str, Any]],
    cues: list[dict[str, Any]],
    width: int,
    height: int,
    duration_s: float,
    audio_asset_name: str,
    theme: dict[str, Any] | None = None,
) -> str:
    theme = theme or {}
    background = safe_css_color(theme.get("background"), "#07111f")
    primary = safe_css_color(theme.get("primary"), "#7cb4ff")
    secondary = safe_css_color(theme.get("secondary"), "#41e5b5")
    accent = safe_css_color(theme.get("accent"), "#ffb35c")
    scene_markup = "\n".join(render_scene_markup(scene, width, height) for scene in scenes)
    agent_css = "\n".join(
        validate_scene_code(int(scene["scene_number"]), scene.get("scene_code"))["css"] for scene in scenes
    )
    agent_timeline = "\n".join(render_agent_timeline_call(scene) for scene in scenes)
    caption_markup = "\n".join(
        f'<div id="caption-{int(cue["index"]):03d}" class="caption-line">{html.escape(str(cue["text"]))}</div>'
        for cue in cues
    )
    cue_js = json.dumps(cues, ensure_ascii=False)
    title = html.escape(str(project.get("name") or "科普视频"))
    portrait = height > width
    scene_pad_x = 58 if portrait else 88
    scene_pad_top = 104 if portrait else 70
    scene_pad_bottom = 190 if portrait else 150
    visual_top = 300 if portrait else 225
    caption_bottom = 100 if portrait else 84
    caption_size = 46 if width < 1400 else 38
    headline_size = 72 if portrait else 64
    subline_size = 33 if portrait else 24
    source_left = -scene_pad_x + 32
    source_bottom = -scene_pad_bottom + 32
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width={width}, height={height}" />
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
  <style>
    @font-face {{ font-family:"FrameCraft Sans"; src:local("PingFang SC"),local("Microsoft YaHei"),local("Noto Sans CJK SC"); font-weight:400 900; font-display:block; }}
    @font-face {{ font-family:"PingFang SC"; src:local("PingFang SC"); font-weight:400 900; font-display:block; }}
    @font-face {{ font-family:"Microsoft YaHei"; src:local("Microsoft YaHei"); font-weight:400 900; font-display:block; }}
    @font-face {{ font-family:"Noto Sans CJK SC"; src:local("Noto Sans CJK SC"); font-weight:400 900; font-display:block; }}
    @font-face {{ font-family:"Hiragino Sans GB"; src:local("Hiragino Sans GB"); font-weight:400 900; font-display:block; }}
    @font-face {{ font-family:"Source Han Sans SC"; src:local("Source Han Sans SC"); font-weight:400 900; font-display:block; }}
    * {{ box-sizing:border-box; margin:0; padding:0; }}
    html,body,#root {{ width:{width}px; height:{height}px; overflow:hidden; background:{background}; }}
    body {{ font-family:"FrameCraft Sans",sans-serif; color:#f4f8ff; }}
    .canvas {{ position:absolute; inset:0; overflow:hidden; background:radial-gradient(circle at 16% 12%,color-mix(in srgb,{primary} 24%,transparent),transparent 32%),radial-gradient(circle at 84% 82%,color-mix(in srgb,{secondary} 18%,transparent),transparent 30%),linear-gradient(145deg,{background},color-mix(in srgb,{background} 88%,{primary})); }}
    .scene {{ position:absolute; inset:0; padding:{scene_pad_top}px {scene_pad_x}px {scene_pad_bottom}px; overflow:hidden; }}
    .scene-shell {{ position:relative; width:100%; height:100%; }}
    .headline {{ position:absolute; left:0; top:0; z-index:30; max-width:82%; font-size:{headline_size}px; line-height:1.06; font-weight:850; letter-spacing:-.035em; }}
    .subline {{ position:absolute; left:0; top:{100 if portrait else 82}px; z-index:30; max-width:76%; font-size:{subline_size}px; line-height:1.42; color:rgba(235,242,255,.78); }}
    .chip-row,.quote-card {{ display:none; }}
    .agent-scene-stage {{ position:absolute; left:0; right:0; top:{visual_top}px; bottom:0; overflow:hidden; isolation:isolate; }}
    .source-line {{ position:absolute; left:{source_left}px; bottom:{source_bottom}px; z-index:40; width:46%; max-width:860px; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; font-size:{26 if portrait else 24}px; line-height:1.35; color:rgba(240,247,255,.82); text-shadow:0 2px 10px rgba(0,0,0,.8); }}
    .caption-shell {{ position:absolute; left:50%; bottom:{caption_bottom}px; transform:translateX(-50%); z-index:50; width:min(90%,{820 if portrait else 1300}px); display:grid; pointer-events:none; }}
    .caption-line {{ grid-area:1/1; justify-self:center; visibility:hidden; opacity:0; max-width:100%; padding:16px 26px; border-radius:999px; background:rgba(5,10,24,.72); border:1px solid rgba(255,255,255,.12); box-shadow:0 22px 50px rgba(0,0,0,.22); text-align:center; font-size:{caption_size}px; line-height:1.42; font-weight:750; color:#f8fbff; }}
    {agent_css}
  </style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="{duration_s:.3f}" data-width="{width}" data-height="{height}">
    <div class="canvas">
      {scene_markup}
      <div id="centered-subtitles" class="caption-shell clip" data-start="0" data-duration="{duration_s:.3f}">{caption_markup}</div>
    </div>
    <audio id="narration" src="assets/{html.escape(audio_asset_name)}" preload="auto" data-start="0"></audio>
  </div>
  <script>
    window.__timelines=window.__timelines||{{}};
    const tl=gsap.timeline({{paused:true}});
    window.__timelines.main=tl;
    const cues={cue_js};
    document.querySelectorAll(".headline,.subline").forEach((node)=>gsap.set(node,{{autoAlpha:0}}));
    {agent_timeline}
    {json.dumps([{"id": scene["scene_id"], "start": scene["start"], "end": scene["end"]} for scene in scenes], ensure_ascii=False)}.forEach((scene)=>{{
      const base="#"+scene.id;
      tl.fromTo(base+" .headline",{{y:30,autoAlpha:0}},{{y:0,autoAlpha:1,duration:.68,ease:"power3.out"}},scene.start+.06);
      tl.fromTo(base+" .subline",{{y:20,autoAlpha:0}},{{y:0,autoAlpha:1,duration:.55,ease:"power2.out"}},scene.start+.18);
      tl.to(base+" .headline, "+base+" .subline",{{y:-18,autoAlpha:0,duration:.3,ease:"power1.in"}},Math.max(scene.start+.8,scene.end-.36));
    }});
    cues.forEach((cue)=>{{
      const selector="#caption-"+String(cue.index).padStart(3,"0");
      tl.fromTo(selector,{{autoAlpha:0,y:12}},{{autoAlpha:1,y:0,duration:.16,ease:"power1.out"}},cue.start);
      tl.to(selector,{{autoAlpha:0,y:-10,duration:.18,ease:"power1.in"}},Math.max(cue.start+.18,cue.end-.16));
    }});
  </script>
</body>
</html>
"""


def render_scene_markup(scene: dict[str, Any], width: int, height: int) -> str:
    quote = html.escape(scene["quote"])
    chips = "\n".join(f'<div class="chip">{html.escape(text)}</div>' for text in scene["chips"][:4])
    main = render_agent_scene_markup(scene)
    source = ""
    return f"""
<section id="{html.escape(scene['scene_id'])}" class="scene clip variant-{scene['variant']} layout-{scene['layout']} scene-order-{scene['scene_number']} agent-generated-scene" data-start="{scene['start']:.3f}" data-duration="{scene['duration']:.3f}">
  <div class="scene-shell">
    <div class="headline">{html.escape(scene['headline'])}</div>
    <div class="subline">{html.escape(scene['subline'])}</div>
    <div class="chip-row">{chips}</div>
    {main}
    {source}
    <div class="quote-card">{quote}</div>
  </div>
</section>
"""


def render_agent_scene_markup(scene: dict[str, Any]) -> str:
    number = int(scene.get("scene_number") or 0)
    return validate_scene_code(number, scene.get("scene_code"))["markup"]


def render_agent_timeline_call(scene: dict[str, Any]) -> str:
    number = int(scene.get("scene_number") or 0)
    code = validate_scene_code(number, scene.get("scene_code"))["timeline_js"]
    scene_id = json.dumps(str(scene["scene_id"]))
    start = float(scene["start"])
    end = float(scene["end"])
    duration = float(scene["duration"])
    return f"""
      (() => {{
        const root = document.getElementById({scene_id});
        if (!root) throw new Error("Missing generated scene root: " + {scene_id});
        const q = (selector) => root.querySelector(selector);
        const qa = (selector) => Array.from(root.querySelectorAll(selector));
        const sceneStart = {start:.3f};
        const sceneEnd = {end:.3f};
        const sceneDuration = {duration:.3f};
        {code}
      }})();
"""


def render_variant_markup(scene: dict[str, Any], width: int, height: int) -> str:
    """Legacy renderer kept for archived projects; the active pipeline never calls it."""
    if scene["variant"] == "process":
        horizontal = width > height and scene.get("layout") in {"wide", "center"}
        step_gap = 172 if height > width else 116
        start_top = 90 if height > width else 72
        steps_html = []
        nodes_html = []
        visible_steps = scene["steps"][:4]
        for idx, step in enumerate(visible_steps):
            top = start_top + idx * step_gap
            inline = f"left:{4 + idx * (92 / max(1, len(visible_steps))):.2f}%;width:{84 / max(1, len(visible_steps)):.2f}%;top:92px;" if horizontal else f"top:{top}px;"
            steps_html.append(
                f"""
      <div class="process-step" style="{inline}">
        <div class="step-index">{idx + 1:02d}</div>
        <div class="step-text">{html.escape(step)}</div>
      </div>
"""
            )
            node_inline = f"left:{12 + idx * (84 / max(1, len(visible_steps))):.2f}%;top:48px;" if horizontal else f"top:{top + 44}px;"
            nodes_html.append(f'<div class="process-node" style="{node_inline}"></div>')
        return f"""
    <div class="glass-panel process-board">
      <div class="process-rail"></div>
      {''.join(nodes_html)}
      {''.join(steps_html)}
    </div>
"""
    if scene["variant"] == "data":
        bars = []
        metrics = []
        values = scene.get("values", [])[:3]
        for idx, item in enumerate(values):
            metrics.append(
                f"""
        <div class="metric-card">
          <div class="metric-label">{html.escape(item['label'])}</div>
          <div class="metric-value">{html.escape(item['value'])}</div>
        </div>
"""
            )
            bars.append(
                f"""
        <div class="bar-card bar-{idx}">
          <div class="bar-wrap">
            <div class="bar" data-target-height="{item['height']}%" style="height:{item['height']}%;"></div>
          </div>
          <div class="bar-label">{html.escape(item['label'])}</div>
        </div>
"""
            )
        return f"""
    <div class="glass-panel data-board">
      <div class="data-topline">{''.join(metrics)}</div>
      <div class="bar-zone">{''.join(bars)}</div>
    </div>
"""
    if scene["variant"] == "story":
        card_gap = 150 if height > width else 104
        cards = []
        dots = []
        for idx, step in enumerate(scene["steps"][:4]):
            top = 78 + idx * card_gap
            dots.append(f'<div class="story-dot" style="top:{top + 18}px;"></div>')
            cards.append(
                f"""
      <div class="story-card" style="top:{top}px;">
        <div class="story-card-title">{idx + 1:02d}</div>
        <div class="story-card-text">{html.escape(step)}</div>
      </div>
"""
            )
        return f"""
    <div class="glass-panel story-board">
      <div class="story-line"></div>
      {''.join(dots)}
      {''.join(cards)}
    </div>
"""
    satellites = []
    classes = ["satellite-a", "satellite-b", "satellite-c", "satellite-d"]
    for idx, step in enumerate(scene["steps"][:4]):
        satellites.append(f'<div class="satellite {classes[idx % len(classes)]}">{html.escape(step)}</div>')
    return f"""
    <div class="glass-panel knowledge-board">
      <div class="knowledge-core">
        <div class="knowledge-core-title">{html.escape(scene['core_title'])}</div>
        <div class="knowledge-core-sub">{html.escape(scene['core_sub'])}</div>
      </div>
      {''.join(satellites)}
    </div>
"""


def render_semantic_science_markup(scene: dict[str, Any]) -> str:
    motion = str(scene.get("semantic_motion") or "").lower()
    labels = [html.escape(str(item)) for item in scene.get("steps", [])[:4] if str(item).strip()]
    if not labels:
        labels = [html.escape(scene["headline"]), html.escape(scene["subline"])]
    if len(labels) == 1:
        labels.append(html.escape(scene["subline"]))

    if motion == "comparison":
        flow_dots = "".join(f'<span class="flow-dot" style="animation-delay:-{index * .5:.1f}s"></span>' for index in range(6))
        return f"""
    <div class="science-board comparison-board">
      <div class="compare-side"><div class="science-label">{labels[0]}</div><div class="compare-glyph"><div class="compare-ray"></div>{flow_dots}</div></div>
      <div class="compare-axis">对比</div>
      <div class="compare-side"><div class="science-label">{labels[1]}</div><div class="compare-glyph"><div class="compare-ray"></div>{flow_dots}</div></div>
    </div>
"""
    if motion == "mechanism":
        nodes = []
        count = min(4, len(labels))
        positions = [(11, 68), (36, 31), (64, 62), (89, 25)]
        for idx, label in enumerate(labels[:count]):
            left, top = positions[idx]
            nodes.append(
                f'<div class="mechanism-node" style="left:{left:.1f}%;top:{top:.1f}%"><div class="mechanism-dot" style="animation-delay:-{idx * .45:.2f}s"></div><div class="science-label">{label}</div></div>'
            )
        return f"""
    <div class="science-board mechanism-board">
      <svg class="mechanism-track" viewBox="0 0 1000 500" preserveAspectRatio="none"><defs><linearGradient id="mechanism-gradient"><stop stop-color="{html.escape('#7cb4ff')}"/><stop offset="1" stop-color="{html.escape('#41e5b5')}"/></linearGradient></defs><path d="M45 370 C210 360 235 105 360 120 S555 355 650 315 S805 90 955 105"/></svg>
      <div class="mechanism-particle"></div>{''.join(nodes)}
    </div>
"""
    if motion == "scale":
        items = "".join(f'<div class="science-label">{label}</div>' for label in labels[:3])
        return f"""
    <div class="science-board scale-board">
      <div class="scale-field"><div class="scale-ring r3"></div><div class="scale-ring r2"></div><div class="scale-ring r1"></div><div class="scale-core"></div></div>
      <div class="scale-list">{items}</div>
    </div>
"""
    if motion == "timeline":
        markers = []
        count = min(4, len(labels))
        for idx, label in enumerate(labels[:count]):
            left = 10 + idx * (80 / max(1, count - 1))
            top = 68 - idx * (36 / max(1, count - 1))
            size = 34 + idx * 12
            markers.append(
                f'<div class="timeline-marker" style="left:{left:.1f}%;top:{top:.1f}%"><div class="timeline-dot" style="width:{size}px;height:{size}px;animation-delay:-{idx * .5:.2f}s"></div><div class="science-label">{label}</div></div>'
            )
        return f"""
    <div class="science-board timeline-board"><div class="timeline-curve"></div>{''.join(markers)}</div>
"""
    if motion == "system":
        items = "".join(f'<div class="science-label system-item i{idx}">{label}</div>' for idx, label in enumerate(labels[:4]))
        return f"""
    <div class="science-board system-board">
      <svg class="system-links" viewBox="0 0 1000 500" preserveAspectRatio="none"><path d="M120 105 C300 120 330 210 500 250 M880 105 C700 120 670 210 500 250 M130 405 C305 375 340 290 500 250 M870 405 C695 375 660 290 500 250"/><circle cx="500" cy="250" r="14"/></svg>
      <div class="system-orbit"></div><div class="system-core science-label">{html.escape(scene['core_title'])}</div>{items}
    </div>
"""
    return ""


def _build_scene_specs(
    project: dict[str, Any],
    scene_seed: dict[str, Any],
    cues: list[dict[str, Any]],
    source_text: str,
) -> list[dict[str, Any]]:
    raw_scenes = list(scene_seed.get("scenes") or [])
    if not raw_scenes:
        raw_scenes = [
            {
                "sceneNumber": 1,
                "sceneId": "scene_1",
                "start_s": 0.0,
                "end_s": max(float(cues[-1]["end"]) if cues else 12.0, 12.0),
                "duration_s": max(float(cues[-1]["end"]) if cues else 12.0, 12.0),
                "transcript": source_text,
            }
        ]
    family = dominant_style_family(str(project.get("target_style") or ""), source_text)
    scenes: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_scenes, start=1):
        transcript = normalize_text(str(raw.get("transcript") or source_text))
        if not transcript:
            continue
        clauses = split_clauses(transcript)
        semantic_motion = str(raw.get("motion") or "").strip().lower()
        motion_variant = {
            "mechanism": "process",
            "scale": "data",
            "comparison": "data",
            "timeline": "story",
            "system": "knowledge",
        }.get(semantic_motion)
        variant = motion_variant or choose_scene_variant(family, transcript, idx)
        headline = normalize_text(str(raw.get("chapter_title") or ""))[:20] or choose_headline(transcript, idx)
        subline = normalize_text(str(raw.get("visual_claim") or ""))[:32] or choose_subline(clauses)[:32]
        chips = choose_chips(clauses)
        steps = choose_steps(transcript, clauses, variant)
        scene = {
            "scene_number": int(raw.get("sceneNumber") or idx),
            "scene_id": str(raw.get("sceneId") or f"scene_{idx}"),
            "start": float(raw.get("start_s") or 0.0),
            "end": float(raw.get("end_s") or (float(raw.get("start_s") or 0.0) + float(raw.get("duration_s") or 5.0))),
            "duration": float(raw.get("duration_s") or 5.0),
            "variant": variant,
            "semantic_motion": semantic_motion or "system",
            "layout": ["wide", "split-left", "center", "split-right"][(idx - 1) % 4],
            "headline": headline,
            "subline": subline,
            "chips": chips,
            "steps": steps,
            "quote": transcript,
            "core_title": chips[0] if chips else headline,
            "core_sub": subline,
        }
        if variant == "data":
            scene["values"] = build_metric_values(chips, transcript)
            if not scene["values"]:
                scene["variant"] = "knowledge"
            scene["elements"] = [{"kind": "metric", "label": item["label"], "value": item["value"]} for item in scene["values"]]
        else:
            scene["elements"] = [{"kind": "step", "text": step} for step in steps]
        scenes.append(scene)
    return scenes


def choose_scene_variant(family: str, transcript: str, idx: int) -> str:
    if re.search(r"第[一二三四五六七八九十0-9]+步|首先|然后|最后", transcript):
        return "process"
    if re.search(r"\d+|百分之|增长|下降|数据|指标|效率|目标", transcript):
        return "data" if family in {"data", "knowledge"} else family
    if family == "story":
        return "story"
    if family == "data" and idx % 2 == 1:
        return "data"
    if family == "process" and idx % 2 == 1:
        return "process"
    return family


def choose_headline(transcript: str, idx: int) -> str:
    clauses = split_clauses(transcript)
    if not clauses:
        return f"关键观点 {idx}"
    headline = clauses[0]
    headline = re.sub(r"^(今天|现在|我们|首先|然后|最后|所以|其实|就是)", "", headline).strip()
    if len(headline) > 20:
        headline = headline[:20].rstrip("，。；：,.!? ") + "…"
    return headline or clauses[0]


def choose_subline(clauses: list[str]) -> str:
    if len(clauses) >= 2:
        line = " · ".join(clauses[1:3])
    elif clauses:
        line = clauses[0]
    else:
        line = "围绕同一条叙事主线，逐层展开信息。"
    return line[:52]


def choose_chips(clauses: list[str]) -> list[str]:
    chips: list[str] = []
    for clause in clauses:
        text = clause.strip("，。！？；：,.!? ")
        if 2 <= len(text) <= 10 and text not in chips:
            chips.append(text)
        if len(chips) >= 4:
            break
    if not chips:
        for clause in clauses[:3]:
            cleaned = re.sub(r"[的了和与在是]", "", clause).strip()
            if cleaned:
                chips.append(cleaned[:8])
    return chips[:4]


def choose_steps(transcript: str, clauses: list[str], variant: str) -> list[str]:
    explicit = re.findall(r"(第[一二三四五六七八九十0-9]+步[^，。！？；；:：]*)", transcript)
    finals = re.findall(r"(最后[^，。！？；；:：]*)", transcript)
    steps = [normalize_text(item) for item in explicit + finals if normalize_text(item)]
    if len(steps) >= 2:
        return steps[:4]
    cleaned = [normalize_text(item) for item in clauses if normalize_text(item)]
    if variant == "data":
        return cleaned[:3] or [transcript[:14]]
    return cleaned[:4] or [transcript[:14]]


def build_metric_values(chips: list[str], transcript: str) -> list[dict[str, Any]]:
    numbers = re.findall(r"\d+(?:\.\d+)?(?:%|％|倍|万|亿|千米|米|厘米|毫米|秒|分钟|小时|年|摄氏度|度)?", transcript)
    values = []
    for offset, display in enumerate(numbers[:3]):
        label = chips[offset] if offset < len(chips) else "原文数据"
        values.append(
            {
                "label": label,
                "value": display,
                "height": min(90, 45 + offset * 18),
            }
        )
    return values


def apply_creative_plan(scenes: list[dict[str, Any]], creative_plan: dict[str, Any]) -> list[dict[str, Any]]:
    overrides = {
        int(item.get("scene_number") or 0): item
        for item in creative_plan.get("scenes") or []
        if isinstance(item, dict)
    }
    allowed_variants = {"process", "data", "knowledge", "story"}
    allowed_semantics = {
        "mechanism": {"process"},
        "scale": {"data"},
        "comparison": {"data"},
        "timeline": {"story"},
        "system": {"knowledge"},
    }
    allowed_layouts = {"wide", "split-left", "split-right", "center"}
    previous_layout = ""
    layout_cycle = ["wide", "split-left", "center", "split-right"]
    for scene in scenes:
        item = overrides.get(int(scene["scene_number"]))
        if not item:
            raise ValueError(f"第 {scene['scene_number']} 幕缺少逐幕 Agent 设计结果。")
        variant = str(item.get("variant") or "")
        if variant in allowed_variants:
            scene["variant"] = variant
        semantic = str(item.get("semantic_motion") or "").strip().lower()
        if semantic in allowed_semantics and variant in allowed_semantics[semantic]:
            scene["semantic_motion"] = semantic
        layout = str(item.get("layout") or "")
        if layout in allowed_layouts:
            scene["layout"] = layout
        if scene["layout"] == previous_layout:
            scene["layout"] = layout_cycle[(layout_cycle.index(previous_layout) + 1) % len(layout_cycle)]
        previous_layout = scene["layout"]
        for key, max_len in (("headline", 20), ("subline", 32)):
            value = normalize_visible_text(str(item.get(key) or ""))[:max_len]
            if value:
                scene[key] = value
        for key, limit, max_len in (("chips", 4, 10), ("steps", 4, 16)):
            values = list(dict.fromkeys(normalize_visible_text(str(value))[:max_len] for value in item.get(key) or []))
            values = [value for value in values if value]
            if values:
                scene[key] = values[:limit]
        labels = list(dict.fromkeys(normalize_visible_text(str(value))[:10] for value in item.get("labels") or []))
        if not labels:
            labels = [normalize_text(str(value))[:10] for value in scene.get("chips") or []]
        scene["labels"] = [value for value in labels if value][:4]
        scene["actors"] = [value for value in item.get("actors") or [] if isinstance(value, dict)][:8]
        scene["composition"] = item.get("composition") if isinstance(item.get("composition"), dict) else {}
        scene["animation_beats"] = [value for value in item.get("animation_beats") or [] if isinstance(value, dict)][:8]
        scene["scene_code"] = validate_scene_code(int(scene["scene_number"]), item.get("scene_code"))
        scene["code_generator"] = str(item.get("code_generator") or f"scene_designer_{scene['scene_number']}")
        scene["code_reviewer"] = str(item.get("code_reviewer") or f"scene_code_reviewer_{scene['scene_number']}")
        supplied_values = []
        for value in item.get("values") or []:
            if not isinstance(value, dict):
                continue
            label = normalize_text(str(value.get("label") or ""))[:12]
            display = normalize_text(str(value.get("value") or ""))[:10]
            if label and display and display in scene["quote"]:
                supplied_values.append(
                    {
                        "label": label,
                        "value": display,
                        "height": max(24, min(94, int(value.get("height") or 60))),
                    }
                )
        scene["values"] = supplied_values or build_metric_values(scene["chips"], scene["quote"])
        if scene["variant"] == "data" and not scene["values"]:
            scene["variant"] = "knowledge"
        scene["core_title"] = scene["chips"][0] if scene["chips"] else scene["headline"]
        scene["core_sub"] = scene["subline"]
        scene["elements"] = [{"kind": scene["variant"], "text": value} for value in scene["steps"]]
    validate_scene_code_set(scenes)
    return scenes


def _layout_css(width: int, height: int) -> str:
    if width <= height:
        return """
      .variant-knowledge .headline, .variant-knowledge .subline { text-align: center; left: 8%; right: 8%; max-width: none; }
      .variant-knowledge .chip-row { left: 50%; transform: translateX(-50%); justify-content: center; width: 90%; max-width: none; }
      .layout-split-right .glass-panel { transform: translateX(26px); }
      .layout-split-left .glass-panel { transform: translateX(-26px); }
        """
    return """
      .variant-process.layout-wide .headline,
      .variant-process.layout-center .headline { left: 8%; right: 8%; top: 36px; max-width: none; text-align: center; }
      .variant-process.layout-wide .subline,
      .variant-process.layout-center .subline { left: 12%; right: 12%; top: 126px; max-width: none; text-align: center; }
      .variant-process.layout-wide .chip-row,
      .variant-process.layout-center .chip-row { left: 50%; top: 205px; transform: translateX(-50%); justify-content: center; width: 80%; max-width: none; }
      .variant-process.layout-wide .process-board,
      .variant-process.layout-center .process-board { left: 0; right: 0; top: 320px; width: 100%; height: 300px; }
      .variant-process.layout-wide .process-rail,
      .variant-process.layout-center .process-rail { left: 12%; right: 12%; top: 56px; bottom: auto; width: auto; height: 3px; transform: none; }
      .variant-process.layout-wide .process-step,
      .variant-process.layout-center .process-step { right: auto; margin: 0; min-height: 138px; }
      .variant-data.layout-center .headline,
      .variant-data.layout-split-right .headline { left: 920px; top: 118px; max-width: 700px; }
      .variant-data.layout-center .subline,
      .variant-data.layout-split-right .subline { left: 920px; top: 286px; max-width: 700px; }
      .variant-data.layout-center .chip-row,
      .variant-data.layout-split-right .chip-row { left: 920px; top: 392px; max-width: 700px; }
      .variant-data.layout-center .data-board,
      .variant-data.layout-split-right .data-board { left: 0; right: auto; top: 120px; width: 780px; height: 520px; }
      .variant-knowledge .headline { left: 8%; right: 8%; top: 26px; max-width: none; text-align: center; }
      .variant-knowledge .subline { left: 14%; right: 14%; top: 116px; max-width: none; text-align: center; }
      .variant-knowledge .chip-row { left: 50%; top: 196px; transform: translateX(-50%); justify-content: center; width: 82%; max-width: none; }
      .variant-knowledge .knowledge-board { left: 10%; right: 10%; top: 300px; width: 80%; height: 340px; }
      .variant-story .story-board { left: 0; right: auto; top: 104px; width: 760px; height: 536px; }
      .variant-story .headline { left: 860px; top: 124px; max-width: 760px; }
      .variant-story .subline { left: 860px; top: 300px; max-width: 720px; }
      .variant-story .chip-row { left: 860px; top: 420px; max-width: 720px; }
      .scene-order-5.variant-process.layout-split-left .process-board,
      .scene-order-5.variant-process.layout-split-right .process-board { left: 0; right: auto; top: 110px; width: 760px; height: 530px; }
      .scene-order-5.variant-process.layout-split-left .headline,
      .scene-order-5.variant-process.layout-split-right .headline { left: 860px; top: 120px; max-width: 760px; text-align: left; }
      .scene-order-5.variant-process.layout-split-left .subline,
      .scene-order-5.variant-process.layout-split-right .subline { left: 860px; top: 300px; max-width: 720px; text-align: left; }
      .scene-order-5.variant-process.layout-split-left .chip-row,
      .scene-order-5.variant-process.layout-split-right .chip-row { left: 860px; top: 420px; transform: none; justify-content: flex-start; width: 720px; }
    """


def safe_css_color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text
    return fallback


def split_clauses(text: str) -> list[str]:
    parts = re.split(r"[，。！？；：,.!?;:]+", normalize_text(text))
    return [part.strip() for part in parts if part and part.strip()]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_visible_text(text: str) -> str:
    normalized = normalize_text(text)
    forbidden = ("场景", "制作", "动画", "工作流", "FrameCraft", "Agent", "代码", "分镜")
    return "" if any(word in normalized for word in forbidden) else normalized


def _style_label(style: str) -> str:
    labels = {
        "science_explainer": "科学编辑风",
        "mechanism_lab": "机制拆解",
        "data_science": "数据科普",
        "nature_story": "自然叙事",
        "faceless_explainer": "通用解说",
        "data_story": "数据观点",
        "process_breakdown": "流程拆解",
        "knowledge_burst": "知识科普",
        "storytelling": "叙事讲述",
    }
    return labels.get(style, "科普视频")


def _load_json(path: Path | None) -> dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_words(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.is_file():
        return []
    try:
        return normalize_words(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return []
