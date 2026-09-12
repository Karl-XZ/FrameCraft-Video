from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .deepseek_api import create_client, deepseek_settings, parse_json_object


def _text_model() -> str:
    settings = deepseek_settings()
    return settings.get("text_model") or settings.get("pro_model") or "deepseek-v4-flash"


def generate_science_brief(topic: str, requirements: str, target_duration: int) -> dict[str, Any]:
    topic = (topic or "").strip()
    if not topic:
        raise RuntimeError("主题模式需要填写科普主题。")
    target_chars = max(80, min(900, int(target_duration * 4.2)))
    response = create_client().chat.completions.create(
        model=_text_model(),
        messages=[
            {
                "role": "system",
                "content": """你是严谨的中文科普总编。只输出 JSON，不输出 Markdown。
输出结构：
{"title":"","audience":"","takeaway":"","chapters":[{"title":"","narration":"","visual_claim":"","motion":"mechanism|scale|comparison|timeline|system","evidence_ids":[]}],"sources":[]}
要求：讲稿口语自然、由问题推进到解释和结论；每章只讲一个核心概念；总字数接近指定值；不写制作术语；避免使用“不是……而是……”句式；不得编造实验、数字、机构、链接或来源标注。当前模型不能联网，因此不生成外部来源；涉及精确数字时只使用通用可靠常识，否则改写为定性表达。""",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "topic": topic,
                        "requirements": (requirements or "").strip(),
                        "target_duration_seconds": target_duration,
                        "target_narration_characters": target_chars,
                        "chapter_count": "4-7",
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        temperature=0.35,
        max_tokens=2600,
        extra_body={"thinking": {"type": "disabled"}},
    )
    brief = parse_json_object(response.choices[0].message.content)
    chapters = [item for item in brief.get("chapters") or [] if str(item.get("narration") or "").strip()]
    if len(chapters) < 3:
        raise RuntimeError("DeepSeek 没有生成完整的科普章节。")
    brief["chapters"] = chapters
    brief = _fit_narration_length(brief, target_chars)
    brief["sources"] = []
    brief["topic"] = topic
    brief["requirements"] = (requirements or "").strip()
    return brief


def _fit_narration_length(brief: dict[str, Any], target_chars: int) -> dict[str, Any]:
    current_chars = sum(len(str(item.get("narration") or "")) for item in brief.get("chapters") or [])
    if current_chars <= int(target_chars * 1.15):
        return brief
    candidate = brief
    for attempt in range(2):
        response = create_client().chat.completions.create(
            model=_text_model(),
            messages=[
                {
                    "role": "system",
                    "content": """你是中文科普讲稿压缩编辑。只输出 JSON：{"chapters":[{"title":"","narration":"","visual_claim":"","motion":"mechanism|scale|comparison|timeline|system","evidence_ids":[]}]}。章节数量和顺序保持不变，保留事实、motion 与 evidence_ids，只压缩 narration、title 和 visual_claim。旁白必须口语自然，不得增加原稿没有的数字或来源，不使用制作术语，严格满足总字数范围。""",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "attempt": attempt + 1,
                            "target_total_characters": {
                                "minimum": int(target_chars * 0.82),
                                "maximum": int(target_chars * (1.05 if attempt else 1.1)),
                            },
                            "brief": candidate,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            temperature=0.15,
            max_tokens=1800,
            extra_body={"thinking": {"type": "disabled"}},
        )
        fitted_payload = parse_json_object(response.choices[0].message.content)
        fitted = fitted_payload.get("brief") if isinstance(fitted_payload.get("brief"), dict) else fitted_payload
        chapters = [item for item in fitted.get("chapters") or [] if str(item.get("narration") or "").strip()]
        if len(chapters) < 3:
            raise RuntimeError("Agent 压缩讲稿时丢失了必要章节。")
        candidate = {**brief, "chapters": chapters}
        fitted_chars = sum(len(str(item.get("narration") or "")) for item in chapters)
        if fitted_chars <= int(target_chars * 1.1):
            return candidate
    candidate["chapters"] = _hard_fit_chapters(candidate["chapters"], int(target_chars * 1.08))
    return candidate


def _hard_fit_chapters(chapters: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    total = sum(len(str(item.get("narration") or "")) for item in chapters) or 1
    result: list[dict[str, Any]] = []
    for item in chapters:
        narration = str(item.get("narration") or "").strip()
        allowance = max(18, int(maximum * len(narration) / total))
        if len(narration) > allowance:
            window = narration[:allowance]
            cut = max(window.rfind(mark) for mark in "。！？；，")
            if cut >= int(allowance * 0.65):
                narration = window[: cut + 1]
            else:
                narration = window.rstrip("，；：、 ") + "。"
        result.append({**item, "narration": narration})
    while sum(len(str(item.get("narration") or "")) for item in result) > maximum:
        longest = max(result, key=lambda item: len(str(item.get("narration") or "")))
        text = str(longest.get("narration") or "").rstrip("。")
        longest["narration"] = text[:-1].rstrip("，；：、 ") + "。"
    return result
