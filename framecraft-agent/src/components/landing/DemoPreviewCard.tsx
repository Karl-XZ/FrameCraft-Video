import React from 'react';
import { PlayCircle } from 'lucide-react';
import GlassCard from '../ui/GlassCard';

export default function DemoPreviewCard() {
  return (
    <GlassCard className="p-4 flex flex-col gap-3 animate-float">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-text-main">成片预览</span>
        <div className="flex items-center gap-1">
          <PlayCircle className="w-3.5 h-3.5 text-info" />
          <span className="text-xs text-info">HyperFrames 实际成片</span>
        </div>
      </div>

      <div className="relative rounded-xl overflow-hidden bg-black aspect-video border border-white/10 shadow-[0_18px_60px_rgba(0,0,0,.35)]">
        <video
          className="h-full w-full object-cover"
          src={`${import.meta.env.BASE_URL}showcase/sky-scattering-premium.mp4`}
          autoPlay
          muted
          loop
          playsInline
          controls
          preload="metadata"
          aria-label="天空散射科普成片预览"
        />
      </div>

      <div className="flex items-center justify-between text-[11px] text-text-muted px-1">
        <span>天空为什么是蓝色的</span>
        <span>科学叙事 · 动态图解 · 同步字幕</span>
      </div>
    </GlassCard>
  );
}
