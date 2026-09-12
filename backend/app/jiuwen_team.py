from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.multi_agent.team import BaseTeam
from openjiuwen.core.multi_agent.team_runtime import CommunicableAgent
from openjiuwen.core.session.session import Session
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.multi_agent.schema.team_card import TeamCard

from .agent_scene_code import validate_scene_code, validate_scene_code_set
from .deepseek_api import create_async_client, deepseek_settings, parse_json_object


AGENT_PROMPTS = {
    "narrative": """你是科学可视化内容导演。依据逐字稿、章节视觉主张与场景时间，仅输出 JSON：
{"strategy":"一句话科学解释路径","scenes":[{"scene_number":1,"headline":"观众可见短标题","subline":"当前概念的解释","chips":["科学关键词"],"steps":["因果或结构节点"]}]}
要求：不改写旁白事实；每场聚焦一个概念；不写制作术语；不虚构数据；避免“不是……而是……”句式；所有文字为简体中文。""",
    "visual": """你是高级科学动态图形导演。依据逐字稿与 visual_claim，仅输出 JSON：
{"theme":{"mood":"","palette":["#hex"],"motion":""},"scenes":[{"scene_number":1,"variant":"process|data|knowledge|story","layout":"wide|split-left|split-right|center","semantic_motion":"mechanism|scale|comparison|timeline|system","visual_metaphor":"","emphasis":""}]}
要求：动画必须解释机制、尺度、对比、时间或系统关系；对象在入场后仍有传播、旋转、增长、连接或状态变化；数据只呈现原稿明确提供的数据；相邻场景不得使用相同布局；禁止卡片轮播和PPT感。视觉语言要从实体机械、空间建筑、地形导航、粒子系统、形态变换、数据图形、分屏对比等不同家族中选择，任何单一的“线+节点”图形家族最多用于两幕。""",
    "timing": """你是动态图形时序与可读性专家。依据逐字稿与场景时间，仅输出 JSON：
{"rhythm":"","scenes":[{"scene_number":1,"density":"low|medium|high","entry_order":["标题","图形"],"hold_seconds":2.0}],"risks":[]}
要求：信息至少可读1.5秒；字幕固定底部居中；来源行位于左下且不与字幕碰撞；场景覆盖完整音频；同屏重点不超过两组。""",
}

ART_DIRECTOR_PROMPT = """你是科普动态图形总导演。综合专家报告，为整片制定独有艺术圣经。只输出 JSON：
{"art_bible":{"concept":"","mood":"","background":"#hex","primary":"#hex","secondary":"#hex","accent":"#hex","warm":"#hex","type_style":"","motion_language":"","continuity_device":""},"scene_assignments":[{"scene_number":1,"visual_language":"","directing_note":"","transition_in":"","transition_out":""}]}
要求：每幕建立专属于当前内容的可视化隐喻；全片至少四种明显不同的视觉家族与空间结构；任何单一图形原语（尤其“线+节点”）最多主导两幕，其余幕必须改用实体机械、空间建筑、地形导航、粒子/流体、形态变换、数据图形或分屏对比等不同语言。禁止调用现成整幕模板或沿用历史样片构图；避免玻璃卡片墙和PPT版式；色彩、字体和转场形成连续性；观众可见文字全为简体中文；不得虚构数字。艺术圣经负责统一质感，不能强迫所有幕复用同一种图形。"""

SCENE_DESIGN_PROMPT = """你是只负责一幕的资深动态图形设计师兼 HyperFrames 前端工程师。你会收到当前幕逐字稿、总导演艺术圣经、画布比例和专家意见。你必须亲自设计并编写这一幕，不能选择母题模板或只写设计说明。只输出一个合法 JSON 对象，所有代码放在 JSON 字符串中：
{"scene_number":1,"headline":"","subline":"","focus":"","labels":[""],"actors":[{"kind":"","label":"","role":""}],"composition":{"flow":"","focus_x":50,"focus_y":55,"visual_scale":90,"depth":"midground"},"animation_beats":[{"at":0.1,"action":"","target":"","meaning":""}],"transition_in":"","transition_out":"","scene_code":{"markup":"<div class=\"agent-scene-stage s01-stage\">...</div>","css":".s01-stage {...}","timeline_js":"tl.fromTo(q('.s01-main'), {...}, {...}, sceneStart + 0.2); ..."}}

代码契约：
1. 根据 scene_number 使用唯一前缀 s01-、s02- 等；markup 根节点必须包含 agent-scene-stage；CSS 和 JS 的所有自定义类都使用该前缀。
2. markup 只写当前幕主视觉，可使用 div、span、svg、path、circle、rect、line、polyline、polygon、text、defs、linearGradient、radialGradient、filter。禁止 script、style、图片、外链、事件属性和表单。
3. CSS 必须为本幕独立构图服务，主视觉至少覆盖可用区域宽度的78%和高度的68%，不能把小图形挤在中央并留下大片无意义空白；不要引用 html、body、#root、.scene 或其他幕；禁止 @import、外部 url() 和外部资源，SVG 内部 url(#id) 可用。
4. timeline_js 运行时提供 tl、gsap、q、qa、sceneStart、sceneEnd、sceneDuration。只能操作本幕 q()/qa() 返回的元素；禁止 document、window、网络、存储、定时器、随机数和动态执行。
5. 至少五个有语义的 tl.fromTo/tl.to 动作，入场时间写成 sceneStart 加偏移，持续变化使用 sceneDuration，退场必须在代码中显式写 sceneEnd 减偏移；不能只让整块画面淡入淡出。角色位移、连线生长、状态切换等变化要有足够幅度，让入场、中段、退场三张抽帧一眼可辨。
6. 画面先表达当前知识关系，再承载少量简体中文标签；标题不超过16字、说明不超过30字、标签不超过10字；不显示制作术语。
7. 禁止出现 premium-stage、pm-*、science-board、process-board、knowledge-board、story-board 等旧模板类名。
8. 每个移动角色都要在 CSS 或 fromTo 起点中声明独立初始位置，并拥有不重叠的终点；逐项核对 SVG 坐标与绝对定位元素，禁止多个元素误用同一个位移值。
9. 观众可见标签字号不得小于28px，短标签不得截断或用省略号；不要额外生成会复述字幕的长句卡片。
10. 横屏主 SVG 使用接近 1600×580 的宽画幅 viewBox，竖屏使用接近 900×1180 的高画幅 viewBox，避免用 1600×900 塞进狭长舞台后整体缩小。主角色直径或短边通常应在140–320单位，主路径描边通常应在8–16单位；3px细线和几十像素小节点只能作为辅助细节，不能承担主视觉。
11. 禁止把“深色底+几条细线+几个小圆点”作为整幕主体。每幕至少包含两个有明确面积的彩色视觉体、一个大尺度状态变化和一种与相邻幕不同的空间组织；视觉体可用实心、渐变或半透明填充，不依赖细线图才能看懂。
12. animation_beats.at 是0到1的整幕进度比例，不是秒数。timeline_js 中所有关键语义动作必须使用 `sceneStart + sceneDuration * 0.12`、`sceneStart + sceneDuration * 0.48`、`sceneStart + sceneDuration * 0.76` 这类归一化定位；禁止把 0.2、0.5、0.8 直接当秒数。早段完成入场，中段持续发生机制变化，后段出现结论状态并退场，至少有一个动作落在整幕32%–82%的区间。"""

SCENE_CODE_REVIEW_PROMPT = """你是 HyperFrames 动画代码审查 Agent。检查单幕设计师提交的 markup、CSS 和 GSAP 时间线，只输出 JSON：
{"pass":true,"issues":[""],"strength":""}
必须判退：代码明显无法执行；缺少当前幕独有的视觉角色；只让整块画面淡入淡出；选择器越过本幕；使用外部网络、定时器或旧模板类；直接形成PPT卡片墙；主要动作与逐字稿完全无关。运行时提供的 q()/qa() 已强制限定在当前幕，它们属于安全选择器；CSS 的本机中文字体族以及 SVG 内部 url(#filterId) 属于允许资源，不得据此判退。静态审查不要凭想象否决可能的像素重叠、SVG 坐标误差或零点几秒的节奏差异，这些问题必须在 HyperFrames 真实渲染后的逐幕三帧视觉验收中判断。整体机制成立且确定性检查通过时应 pass=true，可在 strength 中记录非阻断观察。"""

QUALITY_CRITIC_PROMPT = """你是高级科普视频方案审片人。检查整合后的创意规格是否达到专业动态图形标准，只输出 JSON：
{"pass":true,"score":0,"issues":[{"scene_number":1,"problem":"","fix":""}],"global_fix":""}
检查重点：每幕视觉是否真正解释对应逐字稿；相邻空间结构与运动方向是否明显不同；每幕是否由独立源码形成至少三个视觉角色和三个有顺序的动画节拍；是否存在历史样片换皮、PPT卡片感、无意义装饰、过小主视觉、幕后文案或虚构数据；全片色彩和转场是否连续。你看到的是不含源码的方案摘要，不得臆测 CSS、SVG 坐标、选择器或实际像素表现；这些由代码审查和真实渲染截图验收负责。低于85分必须 pass=false，并给出可以直接执行的逐幕修正。"""


class DeepSeekJsonAgent(CommunicableAgent, BaseAgent):
    def __init__(self, card: AgentCard, system_prompt: str, model: str, max_tokens: int | None = None):
        super().__init__(card=card)
        self.system_prompt = system_prompt
        self.model = model
        self.max_tokens = max_tokens or (1800 if self.model.endswith("-pro") else 1200)

    def configure(self, config: Any) -> "DeepSeekJsonAgent":
        return self

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        clean_inputs = dict(inputs or {}) if isinstance(inputs, dict) else inputs
        image_path = ""
        if isinstance(clean_inputs, dict):
            image_path = str(clean_inputs.pop("_image_path", "") or "")
        payload = json.dumps(clean_inputs, ensure_ascii=False, separators=(",", ":"))
        user_content: Any = payload
        image = Path(image_path) if image_path else None
        if image and image.is_file():
            encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
            user_content = [
                {"type": "text", "text": payload},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ]
        started = time.perf_counter()
        response = await create_async_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.25,
            max_tokens=self.max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
        message = response.choices[0].message
        result = parse_json_object(message.content or getattr(message, "reasoning_content", ""))
        return {
            "agent": self.card.id,
            "model": self.model,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "result": result,
        }

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


class FrameCraftJiuwenTeam(BaseTeam):
    def __init__(self, team_id: str):
        super().__init__(
            card=TeamCard(id=team_id, name=team_id, description="FrameCraft openJiuwen 多智能体视频设计团队"),
            config=TeamConfig(max_agents=24, max_concurrent_messages=24, message_timeout=120),
        )
        cfg = deepseek_settings()
        fast_model = cfg["text_model"]
        cards = {
            name: AgentCard(id=name, name=name, description=f"FrameCraft {name} agent")
            for name in (*AGENT_PROMPTS.keys(), "art_director", "quality_critic", "code_director")
        }
        for name, prompt in AGENT_PROMPTS.items():
            self.add_agent(cards[name], lambda n=name, p=prompt: DeepSeekJsonAgent(cards[n], p, fast_model))
        self.add_agent(
            cards["art_director"],
            lambda: DeepSeekJsonAgent(cards["art_director"], ART_DIRECTOR_PROMPT, cfg["pro_model"]),
        )
        self.add_agent(
            cards["quality_critic"],
            lambda: DeepSeekJsonAgent(cards["quality_critic"], QUALITY_CRITIC_PROMPT, fast_model),
        )
        self.add_agent(
            cards["code_director"],
            lambda: DeepSeekJsonAgent(cards["code_director"], _code_director_prompt(), cfg["pro_model"]),
        )

    async def design(self, payload: dict[str, Any]) -> dict[str, Any]:
        cfg = deepseek_settings()
        revision_numbers = {
            int(value)
            for value in payload.get("revision_scene_numbers") or []
            if str(value).isdigit() and int(value) > 0
        }
        previous_plan = payload.get("previous_plan") if isinstance(payload.get("previous_plan"), dict) else {}
        previous_scenes = {
            int(scene.get("scene_number") or 0): scene
            for scene in previous_plan.get("scenes") or []
            if isinstance(scene, dict) and int(scene.get("scene_number") or 0) > 0
        }
        visual_review = payload.get("visual_review") if isinstance(payload.get("visual_review"), dict) else {}
        contact_sheet_path = str(payload.get("contact_sheet_path") or "")
        source_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"previous_plan", "contact_sheet_path"}
        }
        if previous_plan:
            source_payload["previous_plan_summary"] = _without_all_scene_code(previous_plan)
        for index, raw_scene in enumerate(payload.get("scenes") or []):
            scene_number = int(raw_scene.get("scene_number") or index + 1)
            designer_name = f"scene_designer_{scene_number}"
            designer_card = AgentCard(
                id=designer_name,
                name=designer_name,
                description=f"第 {scene_number} 幕独立动画设计与编码 Agent",
            )
            self.add_agent(
                designer_card,
                lambda c=designer_card: DeepSeekJsonAgent(
                    c, SCENE_DESIGN_PROMPT, cfg["pro_model"], max_tokens=6000
                ),
            )
            reviewer_name = f"scene_code_reviewer_{scene_number}"
            reviewer_card = AgentCard(
                id=reviewer_name,
                name=reviewer_name,
                description=f"第 {scene_number} 幕 HyperFrames 动画代码审查 Agent",
            )
            self.add_agent(
                reviewer_card,
                lambda c=reviewer_card: DeepSeekJsonAgent(
                    c, SCENE_CODE_REVIEW_PROMPT, cfg["pro_model"], max_tokens=1600
                ),
            )
            if revision_numbers and scene_number in revision_numbers:
                observer_name = f"scene_visual_observer_{scene_number}"
                observer_card = AgentCard(
                    id=observer_name,
                    name=observer_name,
                    description=f"第 {scene_number} 幕渲染截图视觉观察 Agent",
                )
                self.add_agent(
                    observer_card,
                    lambda c=observer_card: DeepSeekJsonAgent(
                        c,
                        """你是单幕渲染截图验收 Agent。联系表按每幕入场、中段、退场三帧排列。只检查输入指定的 scene_number，结合全片视觉验收问题，输出 JSON：{\"scene_number\":1,\"visible_state_changes\":[],\"composition_problems\":[],\"readability_problems\":[],\"mandatory_redraw_actions\":[]}。必须基于截图中真实可见内容，不评论其他幕；把主视觉过小、大片空白、动作不可见、文字截断、元素相撞等问题改写为设计师能直接执行的重画动作。若主体是细线和小节点，必须要求改成占满舞台的宽画幅 SVG、140–320 单位的实体角色、8–16 单位主描边和大尺度状态变化；不得只建议把原图等比放大。""",
                        cfg["vision_model"],
                        max_tokens=1200,
                    ),
                )
        await self.runtime.start()
        try:
            started = time.perf_counter()
            parallel = await asyncio.gather(
                *[
                    self.runtime.send(source_payload, recipient=name, sender="leader")
                    for name in AGENT_PROMPTS
                ]
            )
            art_direction = await self.runtime.send(
                {"source": source_payload, "expert_reports": parallel},
                recipient="art_director",
                sender="leader",
            )
            visual_observations: dict[int, dict[str, Any]] = {}
            if revision_numbers:
                observed = await asyncio.gather(
                    *[
                        self.runtime.send(
                            {
                                "_image_path": contact_sheet_path,
                                "scene_number": number,
                                "scene_count": len(payload.get("scenes") or []),
                                "scene": next(
                                    (scene for scene in payload.get("scenes") or [] if int(scene.get("scene_number") or 0) == number),
                                    {},
                                ),
                                "visual_review": visual_review,
                                "instruction": "只观察这一幕对应的三帧，给出可执行的重画要求。",
                            },
                            recipient=f"scene_visual_observer_{number}",
                            sender="quality_critic",
                        )
                        for number in sorted(revision_numbers)
                    ]
                )
                visual_observations = {
                    number: report
                    for number, report in zip(sorted(revision_numbers), observed)
                }

            async def create_or_retain_scene(index: int, raw_scene: dict[str, Any]) -> dict[str, Any]:
                number = int(raw_scene.get("scene_number") or index + 1)
                previous = previous_scenes.get(number)
                if revision_numbers and number not in revision_numbers and previous:
                    return {
                        "agent": f"scene_designer_{number}",
                        "model": "retained_after_visual_review",
                        "elapsed_ms": 0,
                        "retained": True,
                        "result": previous,
                    }
                return await self.runtime.send(
                    {
                        "project": payload.get("project"),
                        "scene": raw_scene,
                        "art_direction": art_direction["result"],
                        "expert_reports": parallel,
                        "previous_design": previous or {},
                        "rendered_visual_review": visual_review,
                        "scene_visual_observation": (visual_observations.get(number) or {}).get("result") or {},
                        "instruction": (
                            "这是渲染截图验收后的整幕重画。必须逐项落实观察 Agent 的 mandatory_redraw_actions，"
                            "重写完整 markup、CSS 与 timeline_js；主视觉至少覆盖可用宽度78%、高度68%，"
                            "三个抽帧时刻必须显示明显不同的状态。"
                            if revision_numbers else "从零设计并编写这一幕的完整动画源码。"
                        ),
                    },
                    recipient=f"scene_designer_{number}",
                    sender="scene_visual_observer" if number in visual_observations else "art_director",
                )

            scene_reports = await asyncio.gather(
                *[
                    create_or_retain_scene(index, raw_scene)
                    for index, raw_scene in enumerate(payload.get("scenes") or [])
                ]
            )

            async def review_scene(index: int, report: dict[str, Any]) -> dict[str, Any]:
                raw_scene = (payload.get("scenes") or [])[index]
                number = int(raw_scene.get("scene_number") or index + 1)
                deterministic_issue = ""
                try:
                    validate_scene_code(number, (report.get("result") or {}).get("scene_code"))
                except ValueError as exc:
                    deterministic_issue = str(exc)
                if report.get("retained"):
                    return {
                        "agent": f"scene_code_reviewer_{number}",
                        "model": "retained_after_visual_review",
                        "elapsed_ms": 0,
                        "retained": True,
                        "result": {"pass": not deterministic_issue, "issues": [], "strength": "沿用已通过代码审查的场景源码"},
                        "deterministic_issue": deterministic_issue,
                    }
                reviewed = await self.runtime.send(
                    {
                        "source_scene": raw_scene,
                        "art_bible": art_direction.get("result") or {},
                        "designer_result": report.get("result") or {},
                        "deterministic_issue": deterministic_issue,
                    },
                    recipient=f"scene_code_reviewer_{number}",
                    sender="art_director",
                )
                reviewed["deterministic_issue"] = deterministic_issue
                return reviewed

            code_reviews = await asyncio.gather(
                *[review_scene(index, report) for index, report in enumerate(scene_reports)]
            )
            for revision_round in range(3):
                failing = [
                    index
                    for index, review in enumerate(code_reviews)
                    if review.get("deterministic_issue") or (review.get("result") or {}).get("pass") is not True
                ]
                if not failing:
                    break

                async def revise_scene(index: int) -> tuple[int, dict[str, Any]]:
                    raw_scene = (payload.get("scenes") or [])[index]
                    number = int(raw_scene.get("scene_number") or index + 1)
                    review = code_reviews[index]
                    result = review.get("result") or {}
                    revised = await self.runtime.send(
                        {
                            "project": payload.get("project"),
                            "scene": raw_scene,
                            "art_direction": art_direction["result"],
                            "expert_reports": parallel,
                            "previous_design": scene_reports[index].get("result") or {},
                            "revision_request": {
                                "round": revision_round + 1,
                                "deterministic_issue": review.get("deterministic_issue"),
                                "review_issues": result.get("issues") or [],
                            },
                            "instruction": "重写整幕设计和全部源码，逐项修复问题，不能只返回差异。",
                        },
                        recipient=f"scene_designer_{number}",
                        sender=f"scene_code_reviewer_{number}",
                    )
                    return index, revised

                revisions = await asyncio.gather(*[revise_scene(index) for index in failing])
                for index, report in revisions:
                    scene_reports[index] = report
                reviewed_revisions = await asyncio.gather(
                    *[review_scene(index, scene_reports[index]) for index in failing]
                )
                for index, review in zip(failing, reviewed_revisions):
                    code_reviews[index] = review
            else:
                failing = [
                    index
                    for index, review in enumerate(code_reviews)
                    if review.get("deterministic_issue") or (review.get("result") or {}).get("pass") is not True
                ]

            if failing:
                index = failing[0]
                review = code_reviews[index]
                number = int((payload.get("scenes") or [])[index].get("scene_number") or index + 1)
                result = review.get("result") or {}
                issues = [review.get("deterministic_issue"), *(result.get("issues") or [])]
                raise RuntimeError(f"第 {number} 幕动画源码三轮重画后仍未通过：{'；'.join(str(v) for v in issues if v)}")

            scene_summaries = [_without_scene_code(report) for report in scene_reports]
            synthesis = await self.runtime.send(
                {
                    "source": source_payload,
                    "expert_reports": parallel,
                    "art_direction": art_direction,
                    "scene_reports": scene_summaries,
                },
                recipient="code_director",
                sender="art_director",
            )
            creative_plan = normalize_creative_plan(
                payload,
                art_direction.get("result") or {},
                [report.get("result") or {} for report in scene_reports],
                synthesis.get("result") or {},
            )
            validate_scene_code_set(creative_plan["scenes"])
            plan_review: dict[str, Any] = {}
            for plan_revision_round in range(1):
                plan_review = await self.runtime.send(
                    {
                        "source": source_payload,
                        "art_direction": art_direction,
                        "creative_plan": _without_all_scene_code(creative_plan),
                        "code_reviews": code_reviews,
                    },
                    recipient="quality_critic",
                    sender="art_director",
                )
                if plan_review["result"].get("pass") and float(plan_review["result"].get("score") or 0) >= 85:
                    break
                raw_issues = plan_review["result"].get("issues") or []
                if isinstance(raw_issues, dict):
                    raw_issues = [raw_issues]
                issue_numbers = {
                    int(issue.get("scene_number") or 0)
                    for issue in raw_issues
                    if isinstance(issue, dict) and int(issue.get("scene_number") or 0) > 0
                }
                if not issue_numbers:
                    issue_numbers = {
                        int(scene.get("scene_number") or index + 1)
                        for index, scene in enumerate(payload.get("scenes") or [])
                    }
                async def revise_from_plan(index: int, raw_scene: dict[str, Any]) -> tuple[int, dict[str, Any]]:
                    number = int(raw_scene.get("scene_number") or index + 1)
                    if number not in issue_numbers:
                        return index, scene_reports[index]
                    revised = await self.runtime.send(
                        {
                            "project": payload.get("project"),
                            "scene": raw_scene,
                            "art_direction": art_direction["result"],
                            "expert_reports": parallel,
                            "previous_design": scene_reports[index].get("result") or {},
                            "revision_request": plan_review["result"],
                            "instruction": (
                                f"这是总质量审片后的第 {plan_revision_round + 1} 轮重画。"
                                "逐项修复本幕问题，重写全部源码，不能只改说明文字。"
                            ),
                        },
                        recipient=f"scene_designer_{number}",
                        sender="quality_critic",
                    )
                    return index, revised

                affected = [
                    (index, raw_scene)
                    for index, raw_scene in enumerate(payload.get("scenes") or [])
                    if int(raw_scene.get("scene_number") or index + 1) in issue_numbers
                ]
                revisions = await asyncio.gather(
                    *[revise_from_plan(index, raw_scene) for index, raw_scene in affected]
                )
                for index, revised in revisions:
                    scene_reports[index] = revised
                reviewed = await asyncio.gather(
                    *[review_scene(index, scene_reports[index]) for index, _ in affected]
                )
                for (index, _), review in zip(affected, reviewed):
                    code_reviews[index] = review
                    review_result = code_reviews[index].get("result") or {}
                    if code_reviews[index].get("deterministic_issue") or review_result.get("pass") is not True:
                        number = int((payload.get("scenes") or [])[index].get("scene_number") or index + 1)
                        raise RuntimeError(f"第 {number} 幕重画后的源码仍未通过代码审查。")
                scene_summaries = [_without_scene_code(report) for report in scene_reports]
                synthesis = await self.runtime.send(
                    {
                        "source": source_payload,
                        "expert_reports": parallel,
                        "art_direction": art_direction,
                        "scene_reports": scene_summaries,
                        "quality_review": plan_review["result"],
                    },
                    recipient="code_director",
                    sender="quality_critic",
                )
                creative_plan = normalize_creative_plan(
                    payload,
                    art_direction.get("result") or {},
                    [report.get("result") or {} for report in scene_reports],
                    synthesis.get("result") or {},
                )
                validate_scene_code_set(creative_plan["scenes"])
            else:
                plan_review["pre_render_status"] = "revised_once_then_deferred_to_rendered_visual_qa"
            return {
                "framework": "openjiuwen",
                "topology": "parallel_experts_then_art_director_then_parallel_scene_coders_then_parallel_code_reviewers_then_integrator",
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "experts": parallel,
                "art_direction": art_direction,
                "scene_agents": scene_reports,
                "code_reviews": code_reviews,
                "visual_observations": visual_observations,
                "plan_review": plan_review,
                "creative_plan": creative_plan,
                "code_director": {k: v for k, v in synthesis.items() if k != "result"},
            }
        finally:
            await self.runtime.stop()

    async def invoke(self, inputs: Any, session: Optional[Session] = None) -> Any:
        return await self.design(dict(inputs or {}))

    async def stream(self, inputs: Any, session: Optional[Session] = None) -> AsyncIterator[Any]:
        yield await self.invoke(inputs, session)


def _code_director_prompt() -> str:
    return """你是 HyperFrames 整合导演。综合专家、总导演与每幕独立设计师的设计摘要，统一文字层级、连续性和转场。逐幕源码由设计师负责，你不能生成、替换或概括源码。只输出 JSON：
{
  "theme":{"background":"#07111f","primary":"#7cb4ff","secondary":"#41e5b5","accent":"#ffb35c","warm":"#ff765a","mood":"","motion_language":"","continuity_device":""},
  "scenes":[{"scene_number":1,"headline":"","subline":"","transition_in":"","transition_out":""}]
}
硬约束：场景数量和编号必须与 source.scenes 完全一致；保留每幕设计师的科学隐喻、actors、composition、animation_beats 和 scene_code；每场文字基于对应 transcript 和 visual_claim；观众可见文字全为简体中文；不得出现“场景、步骤、制作、动画、工作流”等幕后文案；不得编造数字或事实；每个标题最多16字，每个说明最多30字。"""


def _without_scene_code(report: dict[str, Any]) -> dict[str, Any]:
    result = dict(report.get("result") or {})
    result.pop("scene_code", None)
    return {**report, "result": result}


def _without_all_scene_code(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        **plan,
        "scenes": [
            {key: value for key, value in scene.items() if key != "scene_code"}
            for scene in plan.get("scenes") or []
        ],
    }


def normalize_creative_plan(
    source: dict[str, Any],
    art_direction: dict[str, Any],
    scene_reports: list[dict[str, Any]],
    synthesis: dict[str, Any],
) -> dict[str, Any]:
    """Merge all agent layers without allowing the final summarizer to drop scene work."""
    art_bible = art_direction.get("art_bible") if isinstance(art_direction.get("art_bible"), dict) else {}
    theme = synthesis.get("theme") if isinstance(synthesis.get("theme"), dict) else {}
    merged_theme = {**art_bible, **theme}
    assignments = {
        int(item.get("scene_number") or 0): item
        for item in art_direction.get("scene_assignments") or []
        if isinstance(item, dict)
    }
    reports = {
        int(item.get("scene_number") or 0): item
        for item in scene_reports
        if isinstance(item, dict)
    }
    integrated = {
        int(item.get("scene_number") or 0): item
        for item in synthesis.get("scenes") or []
        if isinstance(item, dict)
    }
    scenes: list[dict[str, Any]] = []
    for index, source_scene in enumerate(source.get("scenes") or []):
        number = int(source_scene.get("scene_number") or index + 1)
        assignment = assignments.get(number, {})
        report = reports.get(number, {})
        final = integrated.get(number, {})
        scene = {**report, **final, "scene_number": number}
        scene["headline"] = str(
            final.get("headline") or report.get("headline") or source_scene.get("headline") or ""
        ).strip()
        scene["subline"] = str(
            final.get("subline") or report.get("subline") or source_scene.get("visual_claim") or ""
        ).strip()
        for key in ("actors", "animation_beats", "labels"):
            candidate = final.get(key) or report.get(key) or []
            scene[key] = candidate if isinstance(candidate, list) else []
        composition = final.get("composition") or report.get("composition") or {}
        scene["composition"] = composition if isinstance(composition, dict) else {}
        scene["transition_in"] = str(
            final.get("transition_in") or report.get("transition_in") or assignment.get("transition_in") or ""
        )
        scene["transition_out"] = str(
            final.get("transition_out") or report.get("transition_out") or assignment.get("transition_out") or ""
        )
        scene["scene_code"] = validate_scene_code(number, report.get("scene_code"))
        scene["code_generator"] = f"scene_designer_{number}"
        scene["code_reviewer"] = f"scene_code_reviewer_{number}"
        scenes.append(scene)
    return {"theme": merged_theme, "scenes": scenes}


def run_creative_team(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(FrameCraftJiuwenTeam(f"framecraft_{project_id}").design(payload))


async def review_contact_sheet(image_path: Path, context: dict[str, Any]) -> dict[str, Any]:
    cfg = deepseek_settings()
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = """你是科普成片视觉验收 Agent。这是一张按真实分镜抽取、从左到右再从上到下排列的全片联系表，采样秒数见 media_validation.contact_sample_times_s，采样策略通常为每幕依次抽取入场、中段、退场三帧。context.scenes 是真实分镜清单。联系表中的每格会等比缩到 384 或 480 像素宽，原片通常为 1920 像素宽；必须按元素占单格的相对比例判断最终字号和可读性，不能把缩略图的表观字号直接当作原片字号。字幕容器在原片底部水平居中，只有视觉中心相对单格中心明显偏离时才判定未居中。
检查：每场能否在一秒内看出科学焦点；三张连续采样是否体现明确入场、持续运动或状态演化、退场衔接，不能只是整块卡片淡入淡出；静帧是否能辨认机制、尺度、对比、时间线或系统关系；相邻场景是否采用不同空间结构与运动方向；主视觉是否充分利用画面且没有大片无意义空白；文字是否可读且不拥挤；是否出现幕后制作文案、来源标注、右上角版本号、页码、系统名或调试信息；字幕是否固定在底部居中且始终位于最高视觉层级。字幕是逐字稿的短时分段，片段以逗号结束属于正常断句；只有字形触碰容器边缘、被遮住或残缺时才能判定 CSS 截断。不要要求定性尺度图伪造数值刻度，只有原稿含精确数字时才检查数字一致性。只输出 JSON：{"pass":true,"score":0-100,"issues":[],"summary":""}。存在真实可见问题时 pass 必须为 false。"""
    started = time.perf_counter()
    response = await create_async_client().chat.completions.create(
        model=cfg["vision_model"],
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": json.dumps(context, ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
                ],
            },
        ],
        temperature=0,
        max_tokens=1000,
        extra_body={"thinking": {"type": "disabled"}},
    )
    message = response.choices[0].message
    result = parse_json_object(message.content or getattr(message, "reasoning_content", ""))
    result.update(
        {
            "reviewer": "openjiuwen_visual_qa",
            "model": cfg["vision_model"],
            "contact_sheet": str(image_path),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    )
    return result


def run_visual_review(image_path: Path, context: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(review_contact_sheet(image_path, context))
