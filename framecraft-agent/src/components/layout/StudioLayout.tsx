import React from 'react';
import { Link } from 'react-router-dom';
import { Zap, Sparkles, FileJson, Layers } from 'lucide-react';
import { useProjectStore } from '../../store/projectStore';
import { useStudioWorkflow } from '../../hooks/useStudioWorkflow';
import { api } from '../../api/client';
import StepProgress from '../studio/StepProgress';
import DemoModeBanner from '../layout/DemoModeBanner';
import BottomStatusBar from '../layout/BottomStatusBar';
import AssetUploadZone from '../upload/AssetUploadZone';
import AssetCard from '../upload/AssetCard';
import AssetFilterTabs from '../upload/AssetFilterTabs';
import StudioEmptyState from '../studio/StudioEmptyState';
import AnalysisProgressPanel from '../studio/AnalysisProgressPanel';
import EditPlanCard from '../studio/EditPlanCard';
import VideoPreviewArea from '../studio/VideoPreviewArea';
import MiniTimeline from '../studio/MiniTimeline';
import AgentChatPanel from '../agent/AgentChatPanel';
import AssetDetailDrawer from '../asset/AssetDetailDrawer';
import GradientButton from '../ui/GradientButton';

function GeneratedFileLink({
  title,
  description,
  href,
  icon,
  disabled,
}: {
  title: string;
  description: string;
  href?: string | null;
  icon: React.ReactNode;
  disabled?: boolean;
}) {
  const content = (
    <div className={`rounded-xl border p-3 transition-all ${
      disabled
        ? 'border-white/6 bg-white/[0.025] opacity-45'
        : 'border-white/10 bg-white/[0.045] hover:border-primary/35 hover:bg-primary/10'
    }`}>
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-white/8 flex items-center justify-center flex-shrink-0">{icon}</div>
        <div className="min-w-0">
          <p className="text-xs font-semibold text-text-main truncate">{title}</p>
          <p className="text-[10px] text-text-muted truncate">{description}</p>
        </div>
      </div>
    </div>
  );
  if (disabled || !href) return <div>{content}</div>;
  return <a href={href} download>{content}</a>;
}

export default function StudioLayout() {
  const {
    projectId, step, assets, filter,
    setSelectedAssetId, setShowAssetDrawer,
    generateHyperFramesProgress, generateDraftProgress,
    versions, currentVersionId, setCurrentVersionId, setPreviewUrl, setVersion, previewUrl, error,
    activeJobId, scriptText, inputMode, topic, requirements,
  } = useProjectStore();
  const { startAnalyze, startGenerate, saveScriptText } = useStudioWorkflow();

  const filteredAssets = filter === 'all' ? assets : assets.filter((a) => a.type === filter);
  const currentVersion = versions.find((v) => v.id === currentVersionId) || versions[0];

  const renderCenterPanel = () => {
    switch (step) {
      case 'upload':
        return (
          <div className="flex flex-col items-center h-full justify-center">
            <StudioEmptyState onPrepare={() => void startAnalyze()} busy={Boolean(activeJobId)} />
          </div>
        );
      case 'analyze':
        return <AnalysisProgressPanel />;
      case 'plan':
        return <EditPlanCard />;
      case 'generate':
        return (
          <div className="flex flex-col gap-5 h-full justify-center">
            <div className="space-y-4">
              <div className="glass-card rounded-2xl p-5">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-semibold text-text-main flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-primary-light" />
                    正在生成工程并由当前浏览器渲染
                  </span>
                  <span className="text-xs text-primary-light font-mono">{generateHyperFramesProgress}%</span>
                </div>
                <div className="h-2 bg-white/10 rounded-full overflow-hidden">
                  <div className="h-full bg-btn-gradient rounded-full transition-all duration-200" style={{ width: `${generateHyperFramesProgress}%` }} />
                </div>
              </div>
              <div className="glass-card rounded-2xl p-5">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-sm font-semibold text-text-main flex items-center gap-2">
                    <Zap className="w-4 h-4 text-secondary" />
                    正在校验本地成片、字幕与视觉细节
                  </span>
                  <span className="text-xs text-secondary font-mono">{Math.max(generateDraftProgress, Math.round(generateHyperFramesProgress * 0.9))}%</span>
                </div>
                <div className="h-2 bg-white/10 rounded-full overflow-hidden">
                  <div className="h-full bg-secondary rounded-full transition-all duration-200" style={{ width: `${Math.max(generateDraftProgress, Math.round(generateHyperFramesProgress * 0.9))}%` }} />
                </div>
              </div>
            </div>
          </div>
        );
      case 'result':
        return (
          <div className="flex flex-col gap-4 h-full overflow-y-auto py-2">
            <div className="max-w-[280px] mx-auto">
              <VideoPreviewArea />
            </div>
            {currentVersion?.status && ['awaiting_local_render', 'local_render_ready'].includes(currentVersion.status) && !previewUrl && (
              <GradientButton
                size="lg"
                className="mx-auto rounded-xl"
                onClick={() => void startGenerate()}
                disabled={Boolean(activeJobId)}
              >
                <Zap className="w-4 h-4" />
                在浏览器生成 MP4
              </GradientButton>
            )}
            {currentVersion?.status === 'local_render_failed' && !previewUrl && (
              <p className="mx-auto max-w-md text-center text-xs leading-relaxed text-text-muted">
                这版成片的严格验收发现部分可能的问题。若当前页面已刷新，本机临时视频需要重新生成后才能播放；请在 Agent 对话中查看参考分和可能问题，并决定是否重试设计。
              </p>
            )}
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-text-muted">版本：</span>
              {versions.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  onClick={() => {
                    setCurrentVersionId(v.id);
                    setVersion(`v${v.version_number}.0`);
                    setPreviewUrl(null);
                  }}
                  className={`px-2.5 py-1 rounded-lg text-xs font-medium border transition-all ${
                    v.id === currentVersion?.id
                      ? 'bg-primary/15 text-primary-light border-primary/30'
                      : 'bg-white/4 text-text-muted border-white/8'
                  }`}
                >
                  v{v.version_number}.0
                </button>
              ))}
            </div>
            <MiniTimeline />
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div className="flex flex-col h-full bg-bg-main">
      <DemoModeBanner />
      {error && (
        <div className="px-6 py-2 bg-error/10 text-error text-xs border-b border-error/20">{error}</div>
      )}
      <div className="flex items-center justify-between px-6 py-3 border-b border-white/8 flex-shrink-0">
        <div className="flex items-center gap-4">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-btn-gradient flex items-center justify-center">
              <Zap className="w-4 h-4 text-white" />
            </div>
            <span className="text-sm font-bold text-text-main"><span className="gradient-text">FrameCraft</span> Agent</span>
          </Link>
        </div>
        <StepProgress />
        <div className="w-32" aria-hidden="true" />
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden">
        <div className="w-[30%] border-r border-white/8 flex flex-col p-4 gap-4 overflow-hidden">
          <span className="text-sm font-bold text-text-main">科普输入</span>
          {inputMode === 'media' && <AssetUploadZone />}
          {inputMode === 'topic' && (
            <div className="glass-card rounded-xl p-4 border border-secondary/15 space-y-3">
              <div>
                <span className="text-[10px] uppercase tracking-wider text-secondary">科普主题</span>
                <p className="text-sm font-semibold text-text-main mt-1 leading-relaxed">{topic}</p>
              </div>
              {requirements && (
                <div>
                  <span className="text-[10px] uppercase tracking-wider text-text-muted">补充要求</span>
                  <p className="text-xs text-text-secondary mt-1 leading-relaxed">{requirements}</p>
                </div>
              )}
              <p className="text-[11px] text-text-muted">DeepSeek 将生成讲稿、章节与科学视觉主张，阿里云负责配音。</p>
            </div>
          )}
          {inputMode === 'script' && <div className="glass-card rounded-xl p-3 border border-white/8">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-semibold text-text-main">原始科普文案</span>
              <span className="text-[10px] text-secondary">严格按原文</span>
            </div>
            <textarea
              value={scriptText}
              onChange={(e) => void saveScriptText(e.target.value)}
              placeholder="输入完整科普文案，系统不会擅自改写。"
              className="w-full min-h-28 resize-y rounded-lg bg-white/5 border border-white/8 px-3 py-2 text-xs text-text-main placeholder:text-text-muted focus:outline-none focus:border-primary/40"
            />
          </div>}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-bold text-text-main">项目文件</span>
              <span className="text-[10px] text-text-muted">生成后可查阅下载</span>
            </div>
            <GeneratedFileLink
              title={inputMode === 'media' ? '原始声音文件' : '旁白音频'}
              description="WAV · 声音轨道"
              href={projectId ? api.fileUrl(`/api/projects/${projectId}/source/audio`) : null}
              disabled={!projectId || !currentVersion}
              icon={<Zap className="w-4 h-4 text-secondary" />}
            />
            <GeneratedFileLink
              title="讲稿文件"
              description="TXT · 逐字内容"
              href={projectId ? api.fileUrl(`/api/projects/${projectId}/source/script-file`) : null}
              disabled={!projectId || !currentVersion}
              icon={<FileJson className="w-4 h-4 text-accent" />}
            />
            <GeneratedFileLink
              title="工程文件"
              description="ZIP · HyperFrames HTML"
              href={currentVersion?.hyperframes_url ? api.fileUrl(currentVersion.hyperframes_url) : null}
              disabled={!currentVersion?.hyperframes_url}
              icon={<Layers className="w-4 h-4 text-primary-light" />}
            />
            <GeneratedFileLink
              title="字幕文件"
              description="SRT · 底部居中字幕"
              href={currentVersion?.subtitles_url ? api.fileUrl(currentVersion.subtitles_url) : null}
              disabled={!currentVersion?.subtitles_url}
              icon={<Zap className="w-4 h-4 text-warning" />}
            />
            <GeneratedFileLink
              title="本机成片"
              description="MP4 · 仅当前电脑临时保存"
              href={previewUrl}
              disabled={!previewUrl || !currentVersion}
              icon={<Sparkles className="w-4 h-4 text-primary-light" />}
            />
          </div>
          <AssetFilterTabs />
          <div className="flex-1 overflow-y-auto space-y-2">
            {filteredAssets.map((asset) => (
              <AssetCard key={asset.id} asset={asset} onClick={() => { setSelectedAssetId(asset.id); setShowAssetDrawer(true); }} />
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-hidden p-5">{renderCenterPanel()}</div>
        <div className="w-[20%] border-l border-white/8 flex flex-col overflow-hidden">
          <AgentChatPanel />
        </div>
      </div>
      <BottomStatusBar />
      <AssetDetailDrawer />
    </div>
  );
}
