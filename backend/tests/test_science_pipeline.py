from __future__ import annotations

import json
import tempfile
import unittest
import wave
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.app import aliyun_speech, science_content, store
from backend.app.agent_scene_code import scene_code_fingerprint, validate_scene_code, validate_scene_code_set
from backend.app.ingest import build_audio_scene_seed, build_subtitle_cues, infer_semantic_motion, segment_script_for_tts
from backend.app.jiuwen_team import normalize_creative_plan
from backend.app.managed_faceless_builder import apply_creative_plan, render_agent_scene_markup, render_semantic_science_markup
from backend.app.single_agent import SingleAgentRunner


def wav_bytes(duration_s: float = 0.25) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
        with wave.open(handle.name, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\x00\x00" * int(16000 * duration_s))
        return Path(handle.name).read_bytes()


def generated_scene_code(number: int, shape: str = "orbit") -> dict[str, str]:
    prefix = f"s{number:02d}-"
    return {
        "markup": (
            f'<div class="agent-scene-stage {prefix}stage"><svg class="{prefix}visual" viewBox="0 0 1000 500">'
            f'<path class="{prefix}path" d="M50 250 C300 20 700 480 950 250"/>'
            f'<circle class="{prefix}node {prefix}{shape}" cx="500" cy="250" r="90"/></svg>'
            f'<span class="{prefix}label">核心关系</span></div>'
        ),
        "css": (
            f'.{prefix}stage{{inset:0;position:absolute}} .{prefix}visual{{width:100%;height:100%;overflow:visible}} '
            f'.{prefix}path{{fill:none;stroke:#55c8ff;stroke-width:8}} .{prefix}node{{fill:#ffcc66}} '
            f'.{prefix}label{{position:absolute;left:45%;top:45%;font-size:32px}}'
        ),
        "timeline_js": (
            f"tl.fromTo(q('.{prefix}path'), {{strokeDasharray:1000,strokeDashoffset:1000}}, "
            f"{{strokeDashoffset:0,duration:1}}, sceneStart+.2); "
            f"tl.fromTo(q('.{prefix}node'), {{scale:0,transformOrigin:'center'}}, {{scale:1,duration:.8}}, sceneStart+.7); "
            f"tl.to(q('.{prefix}node'), {{rotation:180,duration:sceneDuration*.36,ease:'none'}}, sceneStart+sceneDuration*.42); "
            f"tl.fromTo(q('.{prefix}label'), {{opacity:0,y:20}}, {{opacity:1,y:0,duration:.5}}, sceneStart+1); "
            f"tl.to(q('.{prefix}stage'), {{opacity:0,duration:.4}}, sceneEnd-.4);"
        ),
    }


class SciencePipelineTests(unittest.TestCase):
    def test_project_list_api_is_private_by_default(self):
        from fastapi.testclient import TestClient

        from backend.app.main import app

        with patch.dict("os.environ", {"FRAMECRAFT_ALLOW_PROJECT_LIST": ""}, clear=False):
            response = TestClient(app).get("/api/projects")
        self.assertEqual(response.status_code, 404)

    def test_chat_before_first_render_asks_for_initial_generation(self):
        state = {
            "projects": {"p1": {"id": "p1", "name": "测试"}},
            "versions": {},
            "chat": {"p1": []},
            "settings": store.default_db()["settings"],
        }

        def mutate(operation):
            return operation(state)

        runner = SingleAgentRunner()
        with (
            patch.object(store, "snapshot", side_effect=lambda: deepcopy(state)),
            patch.object(store, "mutate", side_effect=mutate),
        ):
            message = runner.propose_chat_action("p1", "字幕大一点")

        self.assertEqual(message["status"], "needs_initial_generation")
        self.assertIsNone(message["action"])
        self.assertIn("请先完成初版生成", message["content"])

    @patch("backend.app.single_agent.deepseek_settings", return_value={"text_model": "deepseek-v4-flash"})
    @patch("backend.app.single_agent.create_client")
    def test_dialogue_ai_proposes_fine_tune_action_with_project_code_context(self, client: Mock, _settings: Mock):
        with tempfile.TemporaryDirectory() as temp:
            version_dir = Path(temp) / "v001"
            hf_dir = version_dir / "hyperframes"
            hf_dir.mkdir(parents=True)
            (hf_dir / "index.html").write_text("<div>字幕</div>", encoding="utf-8")
            (version_dir / "timeline.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
            client.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({
                    "intent": "fine_tune",
                    "reply": "可以局部调大字幕。点击“微调”后，我会只改当前工程。",
                    "button_label": "微调",
                    "edit_summary": "把字幕字号调大，保持时间轴不变。",
                    "target_files_hint": ["hyperframes/compositions/captions.html"],
                }, ensure_ascii=False)))]
            )
            state = {
                "projects": {"p1": {"id": "p1", "name": "测试", "status": "ready_to_render"}},
                "versions": {
                    "v001": {
                        "id": "v001", "project_id": "p1", "version_dir": str(version_dir),
                        "status": "local_render_ready", "version_number": 1,
                    }
                },
                "chat": {"p1": []},
                "settings": store.default_db()["settings"],
            }

            def mutate(operation):
                return operation(state)

            runner = SingleAgentRunner()
            with (
                patch.object(store, "snapshot", side_effect=lambda: deepcopy(state)),
                patch.object(store, "mutate", side_effect=mutate),
            ):
                message = runner.propose_chat_action("p1", "字幕大一点")

            sent_payload = json.loads(client.return_value.chat.completions.create.call_args.kwargs["messages"][1]["content"])
            self.assertEqual(message["action"], "fine_tune_video")
            self.assertEqual(message["version_id"], "v001")
            self.assertIn("hyperframes_files", sent_payload["context"])
            self.assertIn("字幕", sent_payload["context"]["hyperframes_files"][0]["preview"])

    def test_regenerate_from_chat_passes_dialogue_summary_to_prompt_ai_job(self):
        state = {
            "projects": {"p1": {"id": "p1"}},
            "versions": {
                "v001": {
                    "id": "v001", "project_id": "p1", "status": "local_render_ready",
                    "render_fps": 24, "visual_revision_round": 0,
                }
            },
            "chat": {
                "p1": [{
                    "id": "msg_action", "action": "regenerate_video", "version_id": "v001",
                    "action_payload": {"user_message": "整体更高级", "edit_summary": "重做全片视觉体系"},
                }]
            },
            "jobs": {},
        }

        def mutate(operation):
            return operation(state)

        runner = SingleAgentRunner()
        with (
            patch.object(store, "snapshot", side_effect=lambda: deepcopy(state)),
            patch.object(store, "mutate", side_effect=mutate),
            patch.object(runner, "start", return_value={"id": "job_regen"}) as start,
        ):
            job = runner.regenerate_from_chat("p1", "v001", "msg_action")

        payload = start.call_args.args[2]
        self.assertEqual(job["id"], "job_regen")
        self.assertTrue(payload["patch"]["regenerate_all"])
        self.assertEqual(payload["patch"]["chat_revision_request"]["edit_summary"], "重做全片视觉体系")
        self.assertIsNone(state["chat"]["p1"][0]["action"])

    def test_unplayable_render_auto_retry_starts_patch_job(self):
        state = {
            "projects": {"p1": {"id": "p1"}},
            "versions": {
                "v001": {
                    "id": "v001", "project_id": "p1", "status": "awaiting_local_render",
                    "render_fps": 24, "visual_revision_round": 0,
                }
            },
            "chat": {"p1": []},
            "jobs": {},
        }

        def mutate(operation):
            return operation(state)

        runner = SingleAgentRunner()
        with (
            patch.object(store, "snapshot", side_effect=lambda: deepcopy(state)),
            patch.object(store, "mutate", side_effect=mutate),
            patch.object(runner, "start", return_value={"id": "job_auto"}) as start,
        ):
            job = runner.retry_unplayable_render("p1", "v001", "播放器启动失败", 2)

        payload = start.call_args.args[2]
        self.assertEqual(job["id"], "job_auto")
        self.assertEqual(payload["patch"]["unplayable_auto_retry_attempt"], 2)
        self.assertEqual(state["versions"]["v001"]["status"], "local_render_failed")
        self.assertIn("第 2/3 次", state["chat"]["p1"][-1]["content"])

    def test_failed_visual_review_waits_for_explicit_user_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project_dir = root / "project"
            version_dir = project_dir / "versions" / "v001"
            version_dir.mkdir(parents=True)
            (version_dir / "timeline.json").write_text(
                json.dumps({"scenes": [{"scene_number": 1, "start_time": 0, "end_time": 10}]}),
                encoding="utf-8",
            )
            contact_sheet = root / "contact.jpg"
            contact_sheet.write_bytes(b"test-image")
            state = {
                "projects": {"p1": {"id": "p1", "name": "测试", "input_mode": "topic"}},
                "versions": {
                    "v001": {
                        "id": "v001",
                        "project_id": "p1",
                        "version_dir": str(version_dir),
                        "status": "awaiting_local_render",
                        "expected_duration_s": 10,
                        "render_fps": 24,
                    }
                },
                "chat": {"p1": []},
                "jobs": {},
            }

            def mutate(operation):
                return operation(state)

            runner = SingleAgentRunner()
            review = {"pass": False, "score": 71, "summary": "构图拥挤", "issues": ["场景 1 标题遮挡主视觉"]}
            with (
                patch.object(store, "snapshot", side_effect=lambda: deepcopy(state)),
                patch.object(store, "mutate", side_effect=mutate),
                patch.object(store, "project_dir", return_value=project_dir),
                patch("backend.app.single_agent.run_visual_review", return_value=review),
                patch.object(runner, "start") as start,
            ):
                result = runner.review_local_render("p1", "v001", contact_sheet, {"duration_s": 10})

            self.assertEqual(result["status"], "awaiting_user_retry")
            self.assertEqual(state["projects"]["p1"]["status"], "awaiting_retry_decision")
            self.assertEqual(state["chat"]["p1"][-1]["action"], "retry_render")
            self.assertIn("71/100", state["chat"]["p1"][-1]["content"])
            start.assert_not_called()

    def test_explicit_retry_passes_review_to_prompt_ai_job(self):
        with tempfile.TemporaryDirectory() as temp:
            project_dir = Path(temp)
            version_dir = project_dir / "versions" / "v001"
            version_dir.mkdir(parents=True)
            review = {"pass": False, "score": 70, "issues": ["第 2 幕信息层级不清"]}
            (version_dir / "agent_visual_review.json").write_text(json.dumps(review), encoding="utf-8")
            (version_dir / "timeline.json").write_text(
                json.dumps({"scenes": [{"scene_number": 1}, {"scene_number": 2}]}), encoding="utf-8"
            )
            state = {
                "projects": {"p1": {"id": "p1"}},
                "versions": {
                    "v001": {
                        "id": "v001", "project_id": "p1", "version_dir": str(version_dir),
                        "status": "local_render_failed", "render_fps": 24,
                    }
                },
                "chat": {"p1": [{"action": "retry_render", "version_id": "v001"}]},
                "jobs": {},
            }

            def mutate(operation):
                return operation(state)

            runner = SingleAgentRunner()
            with (
                patch.object(store, "snapshot", side_effect=lambda: deepcopy(state)),
                patch.object(store, "mutate", side_effect=mutate),
                patch.object(store, "project_dir", return_value=project_dir),
                patch.object(runner, "start", return_value={"id": "job_retry"}) as start,
            ):
                result = runner.retry_local_render("p1", "v001")

            payload = start.call_args.args[2]
            self.assertEqual(result["id"], "job_retry")
            self.assertEqual(payload["patch"]["visual_review"], review)
            self.assertEqual(payload["patch"]["revision_scene_numbers"], [2])
            self.assertIsNone(state["chat"]["p1"][0]["action"])

    def test_script_segmentation_preserves_text(self):
        text = "第一句解释现象。第二句解释原因。第三句给出结论。"
        segments = segment_script_for_tts(text, target_chars=12)
        self.assertEqual("".join(segments), text)
        self.assertGreaterEqual(len(segments), 2)

    def test_semantic_motion_classifies_script_content(self):
        self.assertEqual(infer_semantic_motion("阳光经过大气，随后发生散射。", 1), "mechanism")
        self.assertEqual(infer_semantic_motion("日落路径相比正午更长。", 2), "comparison")
        self.assertEqual(infer_semantic_motion("这个过程经历三个阶段。", 3), "timeline")

    def test_semantic_motion_has_distinct_markup(self):
        base = {
            "headline": "光的传播",
            "subline": "观察传播路径",
            "core_title": "传播",
            "steps": ["光源", "大气", "散射", "观察者"],
        }
        outputs = {
            motion: render_semantic_science_markup({**base, "semantic_motion": motion})
            for motion in ("mechanism", "scale", "comparison", "timeline", "system")
        }
        self.assertEqual(len(set(outputs.values())), 5)
        self.assertTrue(all('class="science-board' in markup for markup in outputs.values()))

    def test_multi_agent_merge_preserves_scene_designer_details(self):
        source = {"scenes": [{"scene_number": 1, "headline": "原始标题", "visual_claim": "光发生散射"}]}
        art = {
            "art_bible": {"background": "#020814", "primary": "#55c8ff"},
            "scene_assignments": [{"scene_number": 1, "motif": "particle_scatter"}],
        }
        report = {
            "scene_number": 1,
            "headline": "粒子改变方向",
            "labels": ["入射光", "散射光"],
            "actors": [{"kind": "ray"}, {"kind": "particle"}, {"kind": "observer"}],
            "animation_beats": [{"at": 0.1}, {"at": 0.4}, {"at": 0.7}],
            "scene_code": generated_scene_code(1),
        }
        merged = normalize_creative_plan(source, art, [report], {"theme": {"accent": "#ffd36a"}, "scenes": []})
        self.assertEqual(len(merged["scenes"][0]["actors"]), 3)
        self.assertEqual(merged["theme"]["background"], "#020814")
        self.assertIn("s01-stage", merged["scenes"][0]["scene_code"]["markup"])

    def test_active_pipeline_requires_agent_generated_scene_code(self):
        scene = {
            "scene_number": 1,
            "headline": "大气中的光",
            "subline": "短波光发生散射",
            "quote": "光与大气分子碰撞后向不同方向散射。",
            "variant": "process",
            "semantic_motion": "mechanism",
            "layout": "wide",
            "chips": ["光", "大气"],
            "steps": ["入射", "碰撞", "散射"],
        }
        with self.assertRaisesRegex(ValueError, "缺少逐幕 Agent"):
            apply_creative_plan([scene], {"scenes": []})

        result = apply_creative_plan([scene], {"scenes": [{"scene_number": 1, "scene_code": generated_scene_code(1)}]})
        self.assertIn("s01-stage", render_agent_scene_markup(result[0]))

    def test_scene_code_rejects_legacy_template_markers(self):
        code = generated_scene_code(1)
        code["markup"] = code["markup"].replace("agent-scene-stage", "agent-scene-stage premium-stage")
        with self.assertRaisesRegex(ValueError, "旧固定模板"):
            validate_scene_code(1, code)

    def test_scene_code_allows_local_svg_filter_but_rejects_external_css_url(self):
        code = generated_scene_code(1)
        code["css"] += " .s01-node{filter:url(#s01-glow)}"
        validate_scene_code(1, code)
        code["css"] += " .s01-stage{background:url(https://example.com/a.png)}"
        with self.assertRaisesRegex(ValueError, "外部资源"):
            validate_scene_code(1, code)

    def test_scene_code_set_rejects_structural_duplicates(self):
        first = {"scene_number": 1, "scene_code": generated_scene_code(1)}
        second = {"scene_number": 2, "scene_code": generated_scene_code(2)}
        self.assertEqual(scene_code_fingerprint(first["scene_code"]), scene_code_fingerprint(second["scene_code"]))
        with self.assertRaisesRegex(ValueError, "近似相同"):
            validate_scene_code_set([first, second])

    def test_caption_does_not_isolate_comma_lead_in(self):
        words = [
            {"text": "与此同时，", "start": 0.0, "end": 0.5},
            {"text": "海底热液", "start": 0.5, "end": 1.1},
            {"text": "也会补充矿物。", "start": 1.1, "end": 2.0},
        ]
        cues = build_subtitle_cues(words)
        self.assertFalse(any(cue["text"] == "与此同时，" for cue in cues))

    def test_caption_merges_too_short_trailing_fragment(self):
        words = [
            {"text": "海水不断蒸发，盐分却大多留在海", "start": 0.0, "end": 2.9},
            {"text": "里。", "start": 2.9, "end": 3.15},
        ]
        cues = build_subtitle_cues(words)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "海水不断蒸发，盐分却大多留在海里")

    def test_caption_breaks_at_natural_punctuation_and_hides_terminal_mark(self):
        words = [
            {"text": "太阳光包含可见光，", "start": 0.0, "end": 1.3},
            {"text": "也包含红外线。", "start": 1.3, "end": 2.6},
            {"text": "人眼只能看到其中一部分。", "start": 2.6, "end": 4.5},
        ]
        cues = build_subtitle_cues(words)
        self.assertEqual(
            [cue["text"] for cue in cues],
            ["太阳光包含可见光", "也包含红外线", "人眼只能看到其中一部分"],
        )

    def test_media_scenes_break_on_complete_sentences(self):
        words = [
            {"text": "海水为什么是咸的？", "start": 0.0, "end": 1.8},
            {"text": "雨水会溶解岩石中的矿物盐。", "start": 1.8, "end": 7.8},
            {"text": "河流将盐分带入海洋。", "start": 7.8, "end": 13.4},
            {"text": "海水蒸发后盐分继续留存。", "start": 13.4, "end": 19.2},
        ]
        seed = build_audio_scene_seed(words)
        self.assertEqual(seed["scene_count"], 3)
        self.assertLessEqual(max(scene["duration_s"] for scene in seed["scenes"]), 8.5)

    @patch.object(aliyun_speech, "speech_settings")
    @patch.object(aliyun_speech.requests, "request")
    def test_aliyun_tts_downloads_audio(self, request: Mock, settings: Mock):
        settings.return_value = {
            "api_key": "test",
            "base_url": "https://speech.example/api/v1",
            "compatible_base_url": "https://speech.example/compatible-mode/v1",
            "tts_model": "qwen3-tts-flash",
            "tts_voice": "Cherry",
            "asr_model": "qwen3-asr-flash",
        }
        request.side_effect = [
            Mock(ok=True, status_code=200, json=lambda: {"request_id": "r1", "output": {"audio": {"url": "https://audio.example/a.wav"}}}),
            Mock(ok=True, status_code=200, content=wav_bytes(), raise_for_status=lambda: None),
        ]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "voice.wav"
            meta = aliyun_speech.synthesize_speech("天空为什么是蓝色的？", output)
            self.assertTrue(output.is_file())
            self.assertEqual(meta["provider"], "aliyun-bailian")
            self.assertGreater(meta["duration_s"], 0)

    @patch.object(aliyun_speech, "speech_settings")
    @patch.object(aliyun_speech.requests, "request")
    def test_aliyun_asr_returns_transcript(self, request: Mock, settings: Mock):
        settings.return_value = {
            "api_key": "test",
            "base_url": "https://speech.example/api/v1",
            "compatible_base_url": "https://speech.example/compatible-mode/v1",
            "tts_model": "qwen3-tts-flash",
            "tts_voice": "Cherry",
            "asr_model": "qwen3-asr-flash",
        }
        request.return_value = Mock(ok=True, status_code=200, json=lambda: {"id": "a1", "choices": [{"message": {"content": "光在大气中发生散射。"}}]})
        with tempfile.TemporaryDirectory() as temp:
            audio = Path(temp) / "input.wav"
            audio.write_bytes(wav_bytes())
            result = aliyun_speech.transcribe_audio_cloud(audio, Path(temp) / "asr")
            self.assertEqual(result["text"], "光在大气中发生散射。")
            self.assertEqual(result["provider"], "aliyun-bailian")

    @patch.object(science_content, "_probe_public_url", return_value="https://example.org/source")
    @patch.object(science_content, "deepseek_settings", return_value={"pro_model": "deepseek-v4-pro"})
    @patch.object(science_content, "create_client")
    def test_topic_mode_creates_structured_brief(self, client: Mock, _settings: Mock, _probe: Mock):
        content = '{"title":"蓝天的颜色","audience":"大众","takeaway":"理解散射","chapters":[{"title":"现象","narration":"抬头看天空，它通常呈现蓝色。","visual_claim":"阳光进入大气","motion":"system","evidence_ids":["S1"]},{"title":"路径","narration":"不同颜色的光会经历不同程度的散射。","visual_claim":"短波更易散开","motion":"mechanism","evidence_ids":["S1"]},{"title":"结论","narration":"我们看到的蓝光来自四面八方。","visual_claim":"散射光进入眼睛","motion":"system","evidence_ids":[]}],"sources":[{"id":"S1","claim":"散射","organization":"示例机构","page":"来源页","url":"https://example.org/source"}]}'
        client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
        brief = science_content.generate_science_brief("天空为什么是蓝色", "面向大众", 45)
        self.assertEqual(len(brief["chapters"]), 3)
        self.assertTrue(brief["sources"][0]["url_verified"])

    @patch.object(science_content, "deepseek_settings", return_value={"pro_model": "deepseek-v4-pro"})
    @patch.object(science_content, "create_client")
    def test_long_topic_brief_accepts_wrapped_compression(self, client: Mock, _settings: Mock):
        compact = {
            "brief": {
                "chapters": [
                    {"title": "现象", "narration": "天空通常呈蓝色。", "visual_claim": "蓝天", "motion": "system", "evidence_ids": []},
                    {"title": "散射", "narration": "短波蓝光更易散开。", "visual_claim": "散射", "motion": "mechanism", "evidence_ids": []},
                    {"title": "结论", "narration": "散射光从各处进入眼睛。", "visual_claim": "观察", "motion": "system", "evidence_ids": []},
                ]
            }
        }
        client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(compact, ensure_ascii=False)))]
        )
        original = {
            "title": "蓝天",
            "sources": [{"id": "S1"}],
            "chapters": [
                {"narration": "这是一段很长的原始讲稿。" * 20},
                {"narration": "继续解释其中的科学机制。" * 20},
                {"narration": "最后给出清晰的科学结论。" * 20},
            ],
        }
        fitted = science_content._fit_narration_length(original, 100)
        self.assertEqual(len(fitted["chapters"]), 3)
        self.assertEqual(fitted["sources"], [{"id": "S1"}])

    @patch.object(science_content, "deepseek_settings", return_value={"pro_model": "deepseek-v4-pro"})
    @patch.object(science_content, "create_client")
    def test_topic_brief_recovers_when_agent_compression_is_still_too_long(self, client: Mock, _settings: Mock):
        oversized = {
            "chapters": [
                {"title": f"章节{i}", "narration": "这段内容解释一个重要科学关系，并保留核心事实。" * 5, "visual_claim": "关系", "motion": "system", "evidence_ids": []}
                for i in range(4)
            ]
        }
        client.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(oversized, ensure_ascii=False)))]
        )
        fitted = science_content._fit_narration_length(oversized, 120)
        total = sum(len(item["narration"]) for item in fitted["chapters"])
        self.assertLessEqual(total, int(120 * 1.08))
        self.assertEqual(len(fitted["chapters"]), 4)


if __name__ == "__main__":
    unittest.main()
