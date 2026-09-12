from __future__ import annotations

import json
import hashlib
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import store
from .aliyun_speech import probe_duration_s, synthesize_speech, transcribe_audio_cloud
from .science_content import generate_science_brief


TTS_SEGMENT_PAUSE_S = 0.3


@dataclass
class PreparedSource:
    mode: str
    source_dir: Path
    source_text: str
    source_audio_path: Path | None
    transcript_path: Path | None
    scene_seed_path: Path | None
    metadata_path: Path


def script_file_path(project_id: str) -> Path:
    return store.project_dir(project_id) / "input" / "script.txt"


def write_script_text(project_id: str, text: str) -> Path:
    path = script_file_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((text or "").strip() + "\n", encoding="utf-8")
    return path


def read_script_text(project: dict[str, Any]) -> str:
    pid = str(project.get("id") or "")
    if not pid:
        return ""
    direct = str(project.get("script_text") or "").strip()
    if direct:
        return direct
    path = script_file_path(pid)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def transcript_json_path(project_id: str) -> Path:
    return store.project_dir(project_id) / "input" / "transcribe" / "transcript.json"


def ensure_version_subtitles(project_id: str, version_dir: Path) -> Path | None:
    subtitle_path = version_dir / "subtitles.srt"
    if subtitle_path.is_file() and subtitle_path.stat().st_size > 0:
        return subtitle_path
    transcript_path = transcript_json_path(project_id)
    if not transcript_path.is_file():
        return None
    try:
        words = json.loads(transcript_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    cues = build_subtitle_cues(words)
    if not cues:
        return None
    subtitle_path.write_text(render_srt(cues), encoding="utf-8")
    return subtitle_path


def prepare_source_bundle(project_id: str) -> PreparedSource:
    data = store.snapshot()
    project = data["projects"].get(project_id) or {}
    assets = [a for a in data["assets"].values() if a["project_id"] == project_id]
    source_dir = store.project_dir(project_id) / "input"
    source_dir.mkdir(parents=True, exist_ok=True)

    input_mode = str(project.get("input_mode") or ("script" if read_script_text(project) else "media"))
    if input_mode == "topic" and project.get("script_user_edited") and read_script_text(project):
        return prepare_script_source(project_id, project, source_dir, read_script_text(project))
    if input_mode == "topic":
        return prepare_topic_source(project_id, project, source_dir)
    if input_mode == "script":
        script_text = read_script_text(project)
        if not script_text:
            raise RuntimeError("文案模式需要填写完整科普文案。")
        return prepare_script_source(project_id, project, source_dir, script_text)
    media_asset = choose_media_asset(assets)
    if not media_asset:
        raise RuntimeError("媒体模式需要上传一条视频或音频。")
    return prepare_media_source(project_id, project, source_dir, Path(str(media_asset["path"])).resolve())


def prepare_topic_source(project_id: str, project: dict[str, Any], source_dir: Path) -> PreparedSource:
    brief_path = source_dir / "science_brief.json"
    if brief_path.is_file():
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
    else:
        brief = generate_science_brief(
            str(project.get("topic") or project.get("name") or ""),
            str(project.get("requirements") or ""),
            int(project.get("target_duration") or 60),
        )
        brief["sources"] = []
        brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    brief["sources"] = []
    segments = [
        {
            "title": str(item.get("title") or f"第{idx}章"),
            "narration": normalize_script_text(str(item.get("narration") or "")),
            "visual_claim": str(item.get("visual_claim") or ""),
            "motion": str(item.get("motion") or "mechanism"),
            "evidence_ids": list(item.get("evidence_ids") or []),
            "evidence_sources": [],
        }
        for idx, item in enumerate(brief.get("chapters") or [], start=1)
        if normalize_script_text(str(item.get("narration") or ""))
    ]
    script_text = "".join(item["narration"] for item in segments)
    write_script_text(project_id, script_text)

    def save_script(data):
        if project_id in data["projects"]:
            data["projects"][project_id]["script_text"] = script_text
            data["projects"][project_id]["script_user_edited"] = False
    store.mutate(save_script)
    return _prepare_synthesized_source(project_id, project, source_dir, script_text, segments, "topic")


def prepare_script_source(
    project_id: str,
    project: dict[str, Any],
    source_dir: Path,
    script_text: str,
) -> PreparedSource:
    transcript_txt = normalize_script_text(script_text)
    segments = [
        {
            "title": derive_chapter_title(text, idx),
            "narration": text,
            "visual_claim": text,
            "motion": infer_semantic_motion(text, idx),
            "evidence_ids": [],
        }
        for idx, text in enumerate(segment_script_for_tts(transcript_txt), start=1)
    ]
    return _prepare_synthesized_source(project_id, project, source_dir, transcript_txt, segments, "script")


def _prepare_synthesized_source(
    project_id: str,
    project: dict[str, Any],
    source_dir: Path,
    script_text: str,
    segments: list[dict[str, Any]],
    mode: str,
) -> PreparedSource:
    script_path = write_script_text(project_id, script_text)
    source_audio = source_dir / "source_audio.wav"
    transcript_dir = source_dir / "transcribe"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_txt = normalize_script_text(script_text)
    script_sha256 = hashlib.sha256(transcript_txt.encode("utf-8")).hexdigest()
    transcript_path = transcript_dir / "transcript.json"
    seed_path = source_dir / "scene_seed.json"
    meta_path = source_dir / "source_bundle.json"
    if all(path.is_file() for path in (source_audio, transcript_path, seed_path, meta_path)):
        cached = json.loads(meta_path.read_text(encoding="utf-8"))
        cached_pause = float((cached.get("tts") or {}).get("segment_pause_s") or 0)
        if (
            cached.get("script_sha256") == script_sha256
            and cached.get("mode") == mode
            and abs(cached_pause - TTS_SEGMENT_PAUSE_S) < 0.001
        ):
            return PreparedSource(
                mode=mode,
                source_dir=source_dir,
                source_text=transcript_txt,
                source_audio_path=source_audio,
                transcript_path=transcript_path,
                scene_seed_path=seed_path,
                metadata_path=meta_path,
            )
    tts_dir = source_dir / "tts"
    tts_dir.mkdir(parents=True, exist_ok=True)
    audio_parts: list[Path] = []
    tts_segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    scene_seed_items: list[dict[str, Any]] = []
    cursor = 0.0
    segment_count = len(segments)
    for idx, segment in enumerate(segments, start=1):
        narration = normalize_script_text(str(segment.get("narration") or ""))
        part = tts_dir / f"scene_{idx:02d}.wav"
        meta = synthesize_speech(narration, part)
        duration = probe_duration_s(part)
        pause_after = TTS_SEGMENT_PAUSE_S if idx < segment_count else 0.0
        scene_duration = duration + pause_after
        segment_words = make_pseudo_timed_words(narration, duration)
        for word in segment_words:
            words.append({**word, "id": f"w{len(words)}", "start": round(word["start"] + cursor, 3), "end": round(word["end"] + cursor, 3)})
        scene_seed_items.append(
            {
                "sceneNumber": idx,
                "sceneId": f"scene_{idx}",
                "start_s": round(cursor, 3),
                "end_s": round(cursor + scene_duration, 3),
                "duration_s": round(scene_duration, 3),
                "transcript": narration,
                "word_count": len(segment_words),
                "words": words[-len(segment_words):] if segment_words else [],
                "chapter_title": segment.get("title"),
                "visual_claim": segment.get("visual_claim"),
                "motion": segment.get("motion"),
                "evidence_ids": segment.get("evidence_ids") or [],
                "evidence_sources": segment.get("evidence_sources") or [],
            }
        )
        audio_parts.append(part)
        tts_segments.append(meta)
        cursor += scene_duration
    concatenate_audio(audio_parts, source_audio, pause_s=TTS_SEGMENT_PAUSE_S)
    transcript_path.write_text(json.dumps(words, ensure_ascii=False, indent=2), encoding="utf-8")
    (transcript_dir / "transcript.txt").write_text(transcript_txt + "\n", encoding="utf-8")
    scene_seed = {
        "mode": mode,
        "total_duration_s": round(probe_duration_s(source_audio), 3),
        "scene_count": len(scene_seed_items),
        "scenes": scene_seed_items,
    }
    seed_path.write_text(json.dumps(scene_seed, ensure_ascii=False, indent=2), encoding="utf-8")
    (source_dir / "transcript.txt").write_text(transcript_txt + "\n", encoding="utf-8")
    meta = {
        "mode": mode,
        "script_path": str(script_path),
        "source_audio_path": str(source_audio),
        "transcript_path": str(transcript_path),
        "scene_seed_path": str(seed_path),
        "target_duration": float(scene_seed.get("total_duration_s") or 0),
        "script_sha256": script_sha256,
        "tts": {"provider": "aliyun-bailian", "segment_pause_s": TTS_SEGMENT_PAUSE_S, "segments": tts_segments},
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return PreparedSource(
        mode=mode,
        source_dir=source_dir,
        source_text=transcript_txt or script_text,
        source_audio_path=source_audio,
        transcript_path=transcript_path,
        scene_seed_path=seed_path,
        metadata_path=meta_path,
    )


def prepare_media_source(
    project_id: str,
    project: dict[str, Any],
    source_dir: Path,
    source_media: Path,
) -> PreparedSource:
    if is_video_file(source_media):
        audio_copy = source_dir / "source_audio.wav"
        extracted = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(source_media), "-vn", "-ac", "1", "-ar", "24000", str(audio_copy)],
            capture_output=True,
            text=True,
            timeout=240,
        )
        if extracted.returncode != 0:
            raise RuntimeError(f"上传视频没有可用音轨：{(extracted.stderr or '').strip()[-600:]}")
        media_kind = "video"
    else:
        audio_copy = source_dir / f"source_audio{source_media.suffix.lower() or '.mp3'}"
        if not audio_copy.exists() or audio_copy.stat().st_mtime < source_media.stat().st_mtime:
            shutil.copy2(source_media, audio_copy)
        media_kind = "audio"
    transcript_path, transcript_txt = transcribe_audio(
        audio_copy,
        source_dir / "transcribe",
        str(project.get("output_language") or "zh"),
    )
    words = json.loads(transcript_path.read_text(encoding="utf-8"))
    scene_seed = build_audio_scene_seed(words)
    seed_path = source_dir / "scene_seed.json"
    seed_path.write_text(json.dumps(scene_seed, ensure_ascii=False, indent=2), encoding="utf-8")
    (source_dir / "transcript.txt").write_text(transcript_txt + "\n", encoding="utf-8")
    meta = {
        "mode": "media",
        "media_kind": media_kind,
        "original_media_path": str(source_media),
        "source_audio_path": str(audio_copy),
        "transcript_path": str(transcript_path),
        "scene_seed_path": str(seed_path),
        "target_duration": float(scene_seed.get("total_duration_s") or 0),
    }
    meta_path = source_dir / "source_bundle.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return PreparedSource(
        mode="media",
        source_dir=source_dir,
        source_text=transcript_txt,
        source_audio_path=audio_copy,
        transcript_path=transcript_path,
        scene_seed_path=seed_path,
        metadata_path=meta_path,
    )


def choose_media_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
    media_assets = [a for a in assets if a.get("file_type") in {"audio", "video"}]
    if media_assets:
        media_assets.sort(key=lambda a: a.get("created_at") or "", reverse=True)
        return media_assets[0]
    return None


def transcribe_audio(audio_path: Path, out_dir: Path, output_language: str) -> tuple[Path, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = out_dir / "transcript.json"
    transcript_text_path = out_dir / "transcript.txt"
    if (
        transcript_path.is_file()
        and transcript_text_path.is_file()
        and transcript_path.stat().st_mtime >= audio_path.stat().st_mtime
    ):
        return transcript_path, transcript_text_path.read_text(encoding="utf-8").strip()

    asr = transcribe_audio_cloud(audio_path, out_dir, output_language if output_language in {"zh", "en", "ja", "ko"} else "zh")
    transcript_txt = normalize_script_text(str(asr.get("text") or ""))
    if not transcript_txt:
        raise RuntimeError("阿里云 ASR 逐字稿为空，无法生成科普视频。")
    total_duration = probe_duration_s(audio_path)
    words = asr.get("words") or make_pseudo_timed_words(transcript_txt, total_duration)
    if not words:
        raise RuntimeError("阿里云 ASR 没有生成可用时间轴。")
    transcript_path.write_text(json.dumps(words, ensure_ascii=False, indent=2), encoding="utf-8")
    transcript_text_path.write_text(transcript_txt + "\n", encoding="utf-8")
    raw_path = out_dir / "transcript_source.json"
    raw_path.write_text(
        json.dumps(
            {
                "provider": asr.get("provider") or "aliyun-bailian",
                "text": transcript_txt,
                "duration_s": round(total_duration, 3),
                "word_count": len(words),
                "model": asr.get("model"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return transcript_path, transcript_txt


def segment_script_for_tts(text: str, target_chars: int = 55) -> list[str]:
    sentences = split_sentences(text)
    if not sentences:
        return [text]
    segments: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > target_chars:
            segments.append(current)
            current = ""
        if len(sentence) > 580:
            for offset in range(0, len(sentence), 520):
                chunk = sentence[offset:offset + 520]
                if current:
                    segments.append(current)
                    current = ""
                segments.append(chunk)
            continue
        current += sentence
    if current:
        segments.append(current)
    return [segment for segment in segments if segment.strip()]


def _concat_list_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'\n"


def concatenate_audio(parts: list[Path], output: Path, pause_s: float = 0.0) -> None:
    if not parts:
        raise RuntimeError("阿里云 TTS 没有生成任何旁白片段。")
    if len(parts) == 1:
        shutil.copy2(parts[0], output)
        return
    list_path = output.parent / "tts_concat.txt"
    concat_parts: list[Path] = []
    if pause_s > 0:
        silence_path = output.parent / "tts_pause_300ms.wav"
        with wave.open(str(parts[0]), "rb") as handle:
            sample_rate = handle.getframerate() or 24000
            channels = handle.getnchannels() or 1
        channel_layout = "mono" if channels == 1 else "stereo"
        silence = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=channel_layout={channel_layout}:sample_rate={sample_rate}",
                "-t",
                f"{pause_s:.3f}",
                "-c:a",
                "pcm_s16le",
                str(silence_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if silence.returncode != 0:
            raise RuntimeError(f"生成 TTS 断句静音失败：{(silence.stderr or '').strip()[-600:]}")
        for index, part in enumerate(parts):
            concat_parts.append(part)
            if index < len(parts) - 1:
                concat_parts.append(silence_path)
    else:
        concat_parts = list(parts)
    list_path.write_text("".join(_concat_list_line(part) for part in concat_parts), encoding="utf-8")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c:a", "pcm_s16le", str(output)],
        capture_output=True,
        text=True,
        timeout=240,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"合并阿里云 TTS 旁白失败：{(proc.stderr or '').strip()[-600:]}")


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".mpeg", ".mpg"}


def build_audio_scene_seed(words: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_words(words)
    if not normalized:
        raise RuntimeError("音频转写结果为空，无法生成解说分镜。")
    scenes: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    min_duration = 4.0
    hard_cap = 8.5
    for word in normalized:
        gap = word["start"] - current[-1]["end"] if current else 0.0
        current.append(word)
        duration = current[-1]["end"] - current[0]["start"]
        boundary = (
            duration >= min_duration and ends_scene_sentence(current[-1]["text"])
        ) or duration >= hard_cap or (gap >= 0.55 and duration >= min_duration)
        if boundary:
            scenes.append(make_audio_scene(len(scenes) + 1, current))
            current = []
    if current:
        if scenes and len(current) < 3:
            scenes[-1] = merge_audio_scene(scenes[-1], current)
        else:
            scenes.append(make_audio_scene(len(scenes) + 1, current))
    total_duration = scenes[-1]["end_s"] if scenes else normalized[-1]["end"]
    return {
        "mode": "audio",
        "total_duration_s": round(total_duration, 3),
        "scene_count": len(scenes),
        "scenes": scenes,
    }


def make_audio_scene(scene_number: int, words: list[dict[str, Any]]) -> dict[str, Any]:
    start = words[0]["start"]
    end = words[-1]["end"]
    transcript = join_words(words)
    return {
        "sceneNumber": scene_number,
        "sceneId": f"scene_{scene_number}",
        "start_s": round(start, 3),
        "end_s": round(end, 3),
        "duration_s": round(end - start, 3),
        "transcript": transcript,
        "word_count": len(words),
        "words": words,
        "chapter_title": derive_chapter_title(transcript, scene_number),
        "visual_claim": transcript,
        "motion": infer_semantic_motion(transcript, scene_number),
    }


def merge_audio_scene(scene: dict[str, Any], words: list[dict[str, Any]]) -> dict[str, Any]:
    merged_words = list(scene.get("words") or []) + words
    scene["end_s"] = round(merged_words[-1]["end"], 3)
    scene["duration_s"] = round(scene["end_s"] - scene["start_s"], 3)
    scene["word_count"] = len(merged_words)
    scene["transcript"] = join_words(merged_words)
    scene["visual_claim"] = scene["transcript"]
    scene["motion"] = infer_semantic_motion(scene["transcript"], int(scene.get("sceneNumber") or 1))
    scene["words"] = merged_words
    return scene


def derive_chapter_title(text: str, index: int) -> str:
    clean = normalize_script_text(text)
    first = re.split(r"[，。！？；：]", clean, maxsplit=1)[0].strip()
    return (first[:18] or f"知识点 {index}").strip()


def infer_semantic_motion(text: str, index: int) -> str:
    clean = normalize_script_text(text)
    if "与此同时" in clean:
        return "system"
    if re.search(r"起初|后来|过去|未来|阶段|时期|演化|历史|周期", clean):
        return "timeline"
    if re.search(r"首先|随后|然后|接着|最终|过程|形成|导致|经过|进入|传递|带入|流入|溶解|留下|补充|积累", clean):
        return "mechanism"
    if re.search(r"相比|区别|相反|更[高低大小多强弱快慢]|一方面|另一方面|而|却", clean):
        return "comparison"
    if re.search(r"倍|比例|范围|尺度|距离|温度|速度|质量|数量|百分|\d", clean):
        return "scale"
    return ["system", "mechanism", "comparison", "scale", "timeline"][(index - 1) % 5]


def build_subtitle_cues(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = normalize_words(words)
    if not normalized:
        return []
    cues: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    max_chars = 18
    max_duration = 2.8
    max_gap = 0.45
    for word in normalized:
        if not current:
            current.append(word)
            continue
        current_text = join_words(current)
        duration = current[-1]["end"] - current[0]["start"]
        gap = word["start"] - current[-1]["end"]
        next_text = join_words(current + [word])
        should_break = (
            ends_caption_sentence(current[-1]["text"])
            or len(next_text) > max_chars
            or duration >= max_duration
            or gap >= max_gap
        )
        if should_break:
            cues.append(_make_subtitle_cue(len(cues) + 1, current))
            current = [word]
        else:
            current.append(word)
    if current:
        cues.append(_make_subtitle_cue(len(cues) + 1, current))
    return merge_short_subtitle_cues(cues)


def merge_short_subtitle_cues(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending = [dict(cue) for cue in cues]
    if len(pending) > 1:
        first = pending[0]
        first_duration = float(first["end"]) - float(first["start"])
        if first_duration < 0.7 or len(str(first["text"]).strip()) <= 2:
            pending[1]["start"] = first["start"]
            pending[1]["text"] = join_words([{"text": first["text"]}, {"text": pending[1]["text"]}])
            pending.pop(0)

    merged: list[dict[str, Any]] = []
    for cue in pending:
        duration = float(cue["end"]) - float(cue["start"])
        text = str(cue["text"])
        if merged and (duration < 0.7 or len(text.strip()) <= 2):
            previous = merged[-1]
            previous["end"] = cue["end"]
            previous["text"] = join_words([{"text": previous["text"]}, {"text": text}])
            continue
        merged.append(dict(cue))
    for index, cue in enumerate(merged, start=1):
        cue["index"] = index
        cleaned = re.sub(r"[，。！？；：,.!?;:、…]+$", "", str(cue["text"]).strip()).strip()
        if cleaned:
            cue["text"] = cleaned
    return merged


def _make_subtitle_cue(index: int, words: list[dict[str, Any]]) -> dict[str, Any]:
    start = float(words[0]["start"])
    end = float(words[-1]["end"])
    if end <= start:
        end = start + 0.25
    return {
        "index": index,
        "start": start,
        "end": end,
        "text": join_words(words),
    }


def render_srt(cues: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for cue in cues:
        lines.extend(
            [
                str(cue["index"]),
                f"{format_srt_timestamp(float(cue['start']))} --> {format_srt_timestamp(float(cue['end']))}",
                str(cue["text"]).strip(),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def format_srt_timestamp(seconds: float) -> str:
    total_ms = max(int(round(seconds * 1000)), 0)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def normalize_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for idx, item in enumerate(words):
        text = str(item.get("text") or item.get("word") or "").strip()
        start = float(item.get("start") or 0)
        end = float(item.get("end") or 0)
        if not text or end <= start:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or f"w{idx}"),
                "text": text,
                "start": round(start, 3),
                "end": round(end, 3),
            }
        )
    return normalized


def make_pseudo_timed_words(text: str, total_duration_s: float) -> list[dict[str, Any]]:
    sentences = split_sentences(normalize_script_text(text))
    if not sentences:
        return []
    sentence_units = [max(1.0, speech_units(sentence)) for sentence in sentences]
    total_units = sum(sentence_units) or 1.0
    cursor = 0.0
    words: list[dict[str, Any]] = []
    for sentence_index, sentence in enumerate(sentences):
        sentence_duration = max(0.4, total_duration_s * sentence_units[sentence_index] / total_units)
        pause = min(0.28, max(0.06, sentence_duration * 0.06)) if sentence_index < len(sentences) - 1 else 0.0
        spoken_duration = max(0.25, sentence_duration - pause)
        tokens = sentence_tokens(sentence)
        if not tokens:
            continue
        token_units = [max(1.0, speech_units(token)) for token in tokens]
        token_total = sum(token_units) or 1.0
        for token_index, token in enumerate(tokens):
            remaining_tokens = len(tokens) - token_index - 1
            duration = max(0.08, spoken_duration * token_units[token_index] / token_total)
            end = min(total_duration_s, cursor + duration)
            if remaining_tokens == 0 and sentence_index == len(sentences) - 1:
                end = total_duration_s
            words.append(
                {
                    "id": f"w{len(words)}",
                    "text": token,
                    "start": round(cursor, 3),
                    "end": round(max(cursor + 0.04, end), 3),
                }
            )
            cursor = round(words[-1]["end"], 3)
        cursor = round(min(total_duration_s, cursor + pause), 3)
    if words:
        words[-1]["end"] = round(max(words[-1]["end"], total_duration_s), 3)
    return normalize_words(words)


def sentence_tokens(sentence: str) -> list[str]:
    raw = re.findall(r"[A-Za-z0-9%]+(?:[./:_-][A-Za-z0-9%]+)*|[\u4e00-\u9fff]{1,4}|[，。！？；：,.!?;:、…]+", sentence)
    tokens: list[str] = []
    for item in raw:
        if re.fullmatch(r"[，。！？；：,.!?;:、…]+", item):
            if tokens:
                tokens[-1] += item
            else:
                tokens.append(item)
            continue
        tokens.append(item)
    return tokens


def speech_units(text: str) -> float:
    stripped = re.sub(r"[，。！？；：,.!?;:、…\s]+", "", text)
    if not stripped:
        return 1.0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", stripped))
    latin = re.findall(r"[A-Za-z0-9%]+", stripped)
    if cjk:
        return float(cjk)
    if latin:
        return float(sum(max(1, len(token) / 3) for token in latin))
    return float(len(stripped))


def normalize_script_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def split_sentences(text: str) -> list[str]:
    items = re.split(r"(?<=[。！？!?；;])\s+|(?<=[。！？!?；;])", text)
    return [item.strip() for item in items if item and item.strip()]


def ends_sentence(text: str) -> bool:
    return bool(re.search(r"[。！？!?；;，,：:]$", text))


def ends_scene_sentence(text: str) -> bool:
    return bool(re.search(r"[。！？!?；;]$", text))


def ends_caption_sentence(text: str) -> bool:
    return bool(re.search(r"[。！？!?；;，,：:]$", text))


def join_words(words: list[dict[str, Any]]) -> str:
    out = ""
    for item in words:
        token = str(item.get("text") or "").strip()
        if not token:
            continue
        if not out:
            out = token
            continue
        if should_insert_space(out[-1], token[:1]):
            out += " " + token
        else:
            out += token
    return re.sub(r"\s+([，。！？；：,.!?;:、…])", r"\1", out).strip()


def should_insert_space(prev_char: str, next_char: str) -> bool:
    prev_is_ascii = bool(re.match(r"[A-Za-z0-9%]", prev_char))
    next_is_ascii = bool(re.match(r"[A-Za-z0-9%]", next_char))
    return prev_is_ascii and next_is_ascii


def build_script_scene_seed(script_text: str, target_duration: int) -> dict[str, Any]:
    cleaned = normalize_script_text(script_text)
    sentences = split_sentences(cleaned)
    if not sentences:
        sentences = [cleaned]
    scene_count = max(4, min(10, math.ceil(max(target_duration, 30) / 9)))
    groups = chunk_sentences(sentences, scene_count)
    scenes = []
    for idx, group in enumerate(groups, start=1):
        transcript = " ".join(group).strip()
        est = estimate_speech_seconds(transcript)
        scenes.append(
            {
                "sceneNumber": idx,
                "sceneId": f"scene_{idx}",
                "duration_s": round(est, 3),
                "transcript": transcript,
                "word_count": len(transcript),
            }
        )
    total = sum(scene["duration_s"] for scene in scenes)
    return {
        "mode": "script",
        "total_duration_s": round(total, 3),
        "scene_count": len(scenes),
        "scenes": scenes,
    }


def chunk_sentences(sentences: list[str], groups: int) -> list[list[str]]:
    if len(sentences) <= groups:
        return [[sentence] for sentence in sentences]
    result: list[list[str]] = []
    remaining = list(sentences)
    remaining_groups = groups
    while remaining:
        take = max(1, math.ceil(len(remaining) / remaining_groups))
        result.append(remaining[:take])
        remaining = remaining[take:]
        remaining_groups -= 1
        if remaining_groups <= 0 and remaining:
            result[-1].extend(remaining)
            break
    return result


def estimate_speech_seconds(text: str) -> float:
    chars = len(re.sub(r"\s+", "", text))
    return max(3.0, chars / 4.8)
