from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


LEGACY_MARKERS = (
    "premium-stage",
    "pm-sun",
    "pm-beam",
    "pm-node",
    "science-board",
    "process-board",
    "knowledge-board",
    "story-board",
)

FORBIDDEN_MARKUP = re.compile(
    r"<\s*(?:script|style|iframe|object|embed|link|meta)\b|\son[a-z]+\s*=|javascript:|https?://",
    re.I,
)
FORBIDDEN_CSS = re.compile(r"@import|expression\s*\(|javascript:|https?://", re.I)
FORBIDDEN_JS = re.compile(
    r"\b(?:document|window|fetch|XMLHttpRequest|WebSocket|eval|Function|localStorage|sessionStorage|indexedDB)\b|"
    r"setTimeout|setInterval|requestAnimationFrame|import\s*\(|https?://",
    re.I,
)


def validate_scene_code(scene_number: int, raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError(f"第 {scene_number} 幕缺少 Agent 动画源码。")
    code = {key: str(raw.get(key) or "").strip() for key in ("markup", "css", "timeline_js")}
    namespace = f"s{scene_number:02d}-"
    if not 180 <= len(code["markup"]) <= 16_000:
        raise ValueError(f"第 {scene_number} 幕 markup 长度不符合要求。")
    if not 160 <= len(code["css"]) <= 16_000:
        raise ValueError(f"第 {scene_number} 幕 CSS 长度不符合要求。")
    if not 220 <= len(code["timeline_js"]) <= 14_000:
        raise ValueError(f"第 {scene_number} 幕 GSAP 时间线长度不符合要求。")
    combined = "\n".join(code.values())
    if any(marker in combined for marker in LEGACY_MARKERS):
        raise ValueError(f"第 {scene_number} 幕引用了旧固定模板。")
    if FORBIDDEN_MARKUP.search(code["markup"]):
        raise ValueError(f"第 {scene_number} 幕 markup 包含禁用标签、事件或外部资源。")
    if FORBIDDEN_CSS.search(code["css"]):
        raise ValueError(f"第 {scene_number} 幕 CSS 包含外部资源或危险表达式。")
    for match in re.finditer(r"url\s*\(\s*['\"]?([^)'\"]+)", code["css"], re.I):
        if not match.group(1).strip().startswith("#"):
            raise ValueError(f"第 {scene_number} 幕 CSS 包含外部资源引用。")
    if FORBIDDEN_JS.search(code["timeline_js"]):
        raise ValueError(f"第 {scene_number} 幕时间线包含越界浏览器能力。")
    if 'class="agent-scene-stage' not in code["markup"] and "class='agent-scene-stage" not in code["markup"]:
        raise ValueError(f"第 {scene_number} 幕 markup 缺少 agent-scene-stage 根节点。")
    if namespace not in code["markup"] or namespace not in code["css"] or namespace not in code["timeline_js"]:
        raise ValueError(f"第 {scene_number} 幕源码没有使用唯一命名空间 {namespace}。")
    if code["timeline_js"].count("tl.") < 3:
        raise ValueError(f"第 {scene_number} 幕至少需要三个 GSAP 时间线动作。")
    if not re.search(r"tl\.(?:to|from|fromTo)\s*\(", code["timeline_js"]):
        raise ValueError(f"第 {scene_number} 幕缺少可见的 GSAP 动画。")
    if not re.search(r"sceneEnd|sceneDuration", code["timeline_js"]):
        raise ValueError(f"第 {scene_number} 幕时间线没有覆盖场景持续阶段或退场。")
    normalized_positions = [
        float(value)
        for value in re.findall(r"sceneDuration\s*\*\s*(0?\.\d+)", code["timeline_js"])
    ]
    if not any(0.32 <= value <= 0.82 for value in normalized_positions):
        raise ValueError(
            f"第 {scene_number} 幕语义动作没有按 sceneDuration 分布到场景中后段，不能把动画全部挤在开头几秒。"
        )
    return code


def scene_code_fingerprint(code: dict[str, str]) -> str:
    source = "\n".join(code[key] for key in ("markup", "css", "timeline_js"))
    source = re.sub(r"s\d{2}-", "sXX-", source)
    source = re.sub(r"#[0-9a-fA-F]{3,8}\b", "#COLOR", source)
    source = re.sub(r"\b\d+(?:\.\d+)?(?:px|%|deg|s)?\b", "N", source)
    source = re.sub(r"\s+", " ", source).strip()
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def validate_scene_code_set(scenes: list[dict[str, Any]]) -> None:
    fingerprints: dict[str, int] = {}
    for scene in scenes:
        number = int(scene.get("scene_number") or 0)
        code = validate_scene_code(number, scene.get("scene_code"))
        fingerprint = scene_code_fingerprint(code)
        if fingerprint in fingerprints:
            raise ValueError(
                f"第 {number} 幕与第 {fingerprints[fingerprint]} 幕使用了近似相同的动画源码，必须重新设计。"
            )
        fingerprints[fingerprint] = number


def write_scene_sources(version_dir: Path, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_dir = version_dir / "scene-sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    for scene in scenes:
        number = int(scene["scene_number"])
        code = validate_scene_code(number, scene.get("scene_code"))
        stem = f"scene_{number:02d}"
        paths = {
            "markup": source_dir / f"{stem}.html",
            "css": source_dir / f"{stem}.css",
            "timeline_js": source_dir / f"{stem}.js",
        }
        for key, path in paths.items():
            path.write_text(code[key] + "\n", encoding="utf-8")
        manifest.append(
            {
                "scene_number": number,
                "generator": str(scene.get("code_generator") or f"scene_designer_{number}"),
                "reviewer": str(scene.get("code_reviewer") or f"scene_code_reviewer_{number}"),
                "fingerprint": scene_code_fingerprint(code),
                "files": {key: str(path.relative_to(version_dir)) for key, path in paths.items()},
            }
        )
    (source_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
