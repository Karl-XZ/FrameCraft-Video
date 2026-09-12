import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Zap, ArrowLeft, Sparkles, Lightbulb, FileText, AudioLines } from 'lucide-react';
import { api } from '../api/client';

const RATIOS = [
  { v: '9:16', label: '9:16 竖屏' },
  { v: '16:9', label: '16:9 横屏' },
  { v: '1:1', label: '1:1 方形' },
];

const STYLES = [
  { v: 'science_explainer', label: '科学编辑风' },
  { v: 'mechanism_lab', label: '机制拆解' },
  { v: 'data_science', label: '数据科普' },
  { v: 'nature_story', label: '自然叙事' },
];

export default function NewProjectPage() {
  const navigate = useNavigate();
  const [name, setName] = useState('未命名项目');
  const [aspectRatio, setAspectRatio] = useState('9:16');
  const [targetStyle, setTargetStyle] = useState('science_explainer');
  const [inputMode, setInputMode] = useState<'topic' | 'script' | 'media'>('topic');
  const [topic, setTopic] = useState('');
  const [requirements, setRequirements] = useState('');
  const [scriptText, setScriptText] = useState('');
  const [targetDuration, setTargetDuration] = useState(60);
  const [keepHyperframes, setKeepHyperframes] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleCreate = async () => {
    if (inputMode === 'topic' && !topic.trim()) {
      setError('请填写要讲清楚的科普主题');
      return;
    }
    if (inputMode === 'script' && !scriptText.trim()) {
      setError('请填写完整科普文案');
      return;
    }
    setError('');
    setSubmitting(true);
    try {
      const p = await api.createProject({
        name: name.trim() || '未命名项目',
        aspect_ratio: aspectRatio,
        target_style: targetStyle,
        target_duration: targetDuration,
        input_mode: inputMode,
        topic,
        requirements,
        script_text: inputMode === 'script' ? scriptText : '',
        output_language: 'zh',
        generate_draft: false,
        keep_hyperframes: keepHyperframes,
      });
      navigate(`/studio?project=${p.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '项目创建失败，请稍后重试');
    } finally {
      setSubmitting(false);
    }
  };

  const fieldCls = 'w-full px-3 py-2.5 rounded-lg bg-white/5 border border-white/10 text-sm text-text-main focus:outline-none focus:border-primary/40';

  return (
    <div className="min-h-screen bg-bg-main relative overflow-x-hidden">
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute top-0 left-0 w-[600px] h-[600px] rounded-full animate-orb-1"
          style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.28), transparent 30%)' }} />
      </div>

      <nav className="relative z-10 flex items-center justify-between px-8 py-5">
        <Link to="/" className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-btn-gradient flex items-center justify-center shadow-glow">
            <Zap className="w-5 h-5 text-white" />
          </div>
            <span className="text-2xl font-extrabold tracking-tight">
            <span className="gradient-text">FrameCraft</span>
            <span className="text-text-main"> Agent</span>
          </span>
        </Link>
        <Link to="/" className="flex items-center gap-2 text-sm text-text-secondary hover:text-text-main transition-colors">
          <ArrowLeft className="w-4 h-4" /> 返回首页
        </Link>
      </nav>

      <main className="relative z-10 max-w-4xl mx-auto w-full px-8 py-6">
        <div className="mb-8">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 border border-primary/20 text-xs text-primary-light font-medium mb-3">
            <Sparkles className="w-3.5 h-3.5" /> 新建项目
          </div>
          <h1 className="text-3xl font-extrabold text-text-main">一键生成科普视频</h1>
          <p className="text-sm text-text-muted mt-1">从一个问题、一篇文案或一段声音出发，生成旁白同步的科学动态图解</p>
        </div>

        <div className="glass-card rounded-2xl p-6 space-y-5">
          <div>
            <label className="block text-xs font-semibold text-text-secondary mb-2">选择输入方式</label>
            <div className="grid grid-cols-3 gap-3">
              {[
                { id: 'topic' as const, title: '输入主题', note: '自动写稿、配音和生成', icon: Lightbulb },
                { id: 'script' as const, title: '输入文案', note: '严格按原文配音和生成', icon: FileText },
                { id: 'media' as const, title: '上传媒体', note: '视频或音频先转写再生成', icon: AudioLines },
              ].map((mode) => {
                const Icon = mode.icon;
                return (
                  <button
                    key={mode.id}
                    type="button"
                    onClick={() => setInputMode(mode.id)}
                    className={`text-left rounded-xl border p-4 transition-all ${inputMode === mode.id ? 'border-secondary/50 bg-secondary/10 shadow-[0_0_24px_rgba(6,182,212,.12)]' : 'border-white/8 bg-white/[0.025] hover:border-white/20'}`}
                  >
                    <Icon className={`w-5 h-5 mb-3 ${inputMode === mode.id ? 'text-secondary' : 'text-text-muted'}`} />
                    <p className="text-sm font-bold text-text-main">{mode.title}</p>
                    <p className="text-xs text-text-muted mt-1 leading-relaxed">{mode.note}</p>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-text-secondary mb-1.5">项目名称</label>
            <input className={fieldCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：黑洞为什么不会吞噬整个宇宙" />
          </div>

          {inputMode === 'topic' && (
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-text-secondary mb-1.5">科普主题</label>
                <textarea className={`${fieldCls} min-h-28 resize-y`} value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="例如：为什么天空是蓝色的？用生活化比喻讲清瑞利散射" />
              </div>
              <div>
                <label className="block text-xs font-semibold text-text-secondary mb-1.5">补充要求（可选）</label>
                <textarea className={`${fieldCls} min-h-28 resize-y`} value={requirements} onChange={(e) => setRequirements(e.target.value)} placeholder="目标观众、必须解释的概念、语气、需要回避的表达等" />
              </div>
            </div>
          )}

          {inputMode === 'script' && (
            <div>
              <label className="block text-xs font-semibold text-text-secondary mb-1.5">完整科普文案</label>
              <textarea className={`${fieldCls} min-h-52 resize-y`} value={scriptText} onChange={(e) => setScriptText(e.target.value)} placeholder="系统会严格按照这里的文字生成阿里云旁白、字幕和科学动画，不会擅自改写。" />
            </div>
          )}

          {inputMode === 'media' && (
            <div className="rounded-xl border border-secondary/20 bg-secondary/5 p-4">
              <p className="text-sm font-semibold text-text-main">创建后上传视频或音频</p>
              <p className="text-xs text-text-muted mt-1 leading-relaxed">系统会保留原音频；视频只提取音轨，然后使用阿里云 ASR 生成逐字稿、字幕和科普分镜。</p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-semibold text-text-secondary mb-1.5">视频比例</label>
              <select className={fieldCls} value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value)}>
                {RATIOS.map((r) => <option key={r.v} value={r.v}>{r.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-text-secondary mb-1.5">目标时长（秒）</label>
              <input type="number" min={15} max={180} className={fieldCls} value={targetDuration}
                onChange={(e) => setTargetDuration(Number(e.target.value) || 60)} />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-text-secondary mb-1.5">视频风格</label>
            <select className={fieldCls} value={targetStyle} onChange={(e) => setTargetStyle(e.target.value)}>
              {STYLES.map((s) => <option key={s.v} value={s.v}>{s.label}</option>)}
            </select>
          </div>
          <label className="flex items-center justify-between py-2 cursor-pointer">
            <span className="text-sm text-text-secondary">保留 HyperFrames 源工程</span>
            <input type="checkbox" checked={keepHyperframes} onChange={(e) => setKeepHyperframes(e.target.checked)}
              className="w-4 h-4 accent-primary" />
          </label>

          <button
            type="button"
            disabled={submitting}
            onClick={() => void handleCreate()}
            className="gradient-btn w-full px-5 py-3 rounded-xl text-sm font-bold flex items-center justify-center gap-2 shadow-glow disabled:opacity-60"
          >
            <Zap className="w-4 h-4" /> {submitting ? '创建中…' : inputMode === 'media' ? '创建并上传媒体' : '创建科普视频项目'}
          </button>
          {error && <p className="text-xs text-red-300 text-center">{error}</p>}
        </div>
      </main>
    </div>
  );
}
