import React from 'react';
import { Clock, FileText, Layers, Play, Sparkles } from 'lucide-react';
import GlassCard from '../ui/GlassCard';
import GradientButton from '../ui/GradientButton';
import { useProjectStore } from '../../store/projectStore';
import { useStudioWorkflow } from '../../hooks/useStudioWorkflow';

export default function EditPlanCard() {
  const { editPlan, jobWarnings, activeJobId, scriptText } = useProjectStore();
  const { saveScriptText, startGenerate } = useStudioWorkflow();

  const planWarnings = [
    ...(jobWarnings || []),
    ...(editPlan?.meta?.llm_note
      ? [{ code: 'edit_plan_llm', message: editPlan.meta.llm_note }]
      : []),
  ];

  if (!editPlan) {
    return (
      <div className="flex items-center justify-center h-full text-text-muted text-sm">
        剪辑方案生成中...
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-5 h-full justify-center">
      <GlassCard className="rounded-2xl p-6 space-y-5">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-bold text-text-main flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-primary-light" />
            科普视频方案
          </h3>
          <span className="px-3 py-1 rounded-full bg-primary/15 text-primary-light text-xs font-semibold border border-primary/20">
            Agent 方案
          </span>
        </div>

        {planWarnings.length > 0 && (
          <div className="rounded-lg border border-warning/30 bg-warning/10 p-3 space-y-1">
            <p className="text-xs font-semibold text-warning">Agent 方案提示</p>
            {planWarnings.map((w, i) => (
              <p key={i} className="text-xs text-text-secondary leading-relaxed">
                · {w.message}
              </p>
            ))}
          </div>
        )}

        <div className="flex gap-4 flex-wrap">
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/8">
            <Play className="w-3.5 h-3.5 text-secondary" />
            <span className="text-xs text-text-secondary">{editPlan.video_concept}</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/8">
            <Clock className="w-3.5 h-3.5 text-accent" />
            <span className="text-xs text-text-secondary">预估 {editPlan.target_duration} 秒</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-secondary/8 border border-secondary/15">
            <Sparkles className="w-3.5 h-3.5 text-secondary" />
            <span className="text-xs text-text-secondary">默认在当前浏览器执行 HyperFrames 真实渲染</span>
          </div>
        </div>

        <div className="space-y-2">
          <span className="text-xs font-semibold text-secondary uppercase tracking-wider flex items-center gap-1.5">
            <FileText className="w-3.5 h-3.5" />
            讲稿
          </span>
          <textarea
            value={scriptText}
            onChange={(event) => void saveScriptText(event.target.value)}
            placeholder="这里会显示生成或上传转写后的讲稿，你可以在确认生成前修改。"
            className="w-full min-h-40 resize-y rounded-xl bg-white/5 border border-white/10 px-4 py-3 text-sm leading-relaxed text-text-main placeholder:text-text-muted focus:outline-none focus:border-primary/40"
          />
          <p className="text-[11px] text-text-muted">修改后会严格按当前讲稿生成旁白、字幕和画面。</p>
        </div>

        <div className="space-y-2">
          <span className="text-xs font-semibold text-accent uppercase tracking-wider flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5" />
            科学图解计划
          </span>
          <div className="space-y-2 max-h-48 overflow-y-auto">
            {(editPlan.broll_plan || []).map((item, i) => (
              <div key={i} className="flex items-center gap-3 p-2.5 rounded-lg bg-white/4 border border-white/6">
                <span className="text-xs font-mono text-secondary w-12 flex-shrink-0">
                  {typeof item.time === 'number' ? `${item.time.toFixed(0)}s` : item.time}
                </span>
                <span className="text-xs text-text-secondary flex-1">{item.text}</span>
                <span className="text-xs text-text-muted">{item.source}</span>
              </div>
            ))}
            {!editPlan.broll_plan?.length && (
              <p className="text-xs text-text-muted">本次将使用机制图、尺度变化、关系图和字幕解释核心概念</p>
            )}
          </div>
        </div>
      </GlassCard>

      <div className="flex gap-3">
        <GradientButton size="lg" className="flex-1 rounded-xl" onClick={() => void startGenerate()} disabled={Boolean(activeJobId)}>
          <Sparkles className="w-4 h-4" />
          {activeJobId ? 'Agent 任务运行中' : '确认生成并在浏览器渲染'}
        </GradientButton>
      </div>
    </div>
  );
}
