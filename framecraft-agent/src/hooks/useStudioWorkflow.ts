import { useCallback, useRef } from 'react';
import {
  api,
  type BackendChatMessage,
  type BackendJob,
  formatDuration,
  formatSize,
  mapAssetType,
} from '../api/client';
import { useProjectStore, type Asset } from '../store/projectStore';
import { checkLocalRenderer, renderLocally } from '../api/localRenderer';

const TERMINAL_JOB_STATUSES = ['completed', 'failed', 'cancelled', 'needs_input'];

function newClientId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `local_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function mapChatMessage(m: BackendChatMessage) {
  return {
    id: m.id,
    role: m.role as 'user' | 'agent',
    text: m.content,
    timestamp: Date.parse(m.created_at) || Date.now(),
    patch: m.patch,
    action: m.action,
    versionId: m.version_id,
  };
}

function isBrowserEnvironmentError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error || '');
  return /HTTPS|Chrome|Edge|浏览器不支持|当前设备无法|安全连接|编码/.test(message);
}

export function useStudioWorkflow() {
  const store = useProjectStore();
  const jobEsRef = useRef<EventSource | null>(null);
  const jobLogCountsRef = useRef<Record<string, number>>({});

  const ensureProject = useCallback(async () => {
    if (store.projectId) return store.projectId;
    const requestedProjectId = new URLSearchParams(window.location.search).get('project');
    if (requestedProjectId) {
      await api.getProject(requestedProjectId);
      store.setProjectId(requestedProjectId);
      return requestedProjectId;
    }
    const p = await api.createProject({
      name: 'Agent 解说项目',
      aspect_ratio: store.videoRatio,
      target_duration: store.targetDuration,
      target_style: store.targetStyle,
      input_mode: store.inputMode,
      topic: store.topic,
      requirements: store.requirements,
      script_text: store.scriptText,
      output_language: store.outputLanguage,
      generate_draft: store.generateDraft,
      keep_hyperframes: store.keepHyperframes,
    });
    store.setProjectId(p.id);
    return p.id;
  }, [store]);

  const refreshAssets = useCallback(async (projectId: string) => {
    const list = await api.listAssets(projectId);
    const mapped: Asset[] = list.map((a) => ({
      id: a.id,
      filename: a.file_name,
      type: mapAssetType(a.user_label, a.file_type),
      duration: formatDuration(a.duration),
      size: formatSize(a.size),
      note: a.user_note,
      status: a.analysis_status === 'completed' ? '分析完成' : a.analysis_status === 'transcribed' ? '已转写' : '已上传',
      thumbnail: a.thumbnail_url ? api.fileUrl(a.thumbnail_url) : undefined,
      mustUse: a.must_use,
      priority: a.priority,
    }));
    store.setAssets(mapped);
  }, [store]);

  const loadProject = useCallback(async (projectId: string) => {
    store.setError(null);
    const p = await api.getProject(projectId);
    store.setProjectId(p.id);
    store.setVideoRatio(p.aspect_ratio);
    store.setTargetDuration(p.target_duration);
    store.setTargetStyle(p.target_style);
    if (p.input_mode) store.setInputMode(p.input_mode);
    if (typeof p.topic === 'string') store.setTopic(p.topic);
    if (typeof p.requirements === 'string') store.setRequirements(p.requirements);
    if (p.output_language) store.setOutputLanguage(p.output_language);
    if (typeof p.script_text === 'string') store.setScriptText(p.script_text);
    if (typeof p.generate_draft === 'boolean') store.setGenerateDraft(p.generate_draft);
    if (typeof p.keep_hyperframes === 'boolean') store.setKeepHyperframes(p.keep_hyperframes);
    await refreshAssets(projectId);
    const versions = await api.listVersions(projectId);
    store.setVersions(versions);
    const pendingVersion = versions.find((version) =>
      ['awaiting_local_render', 'local_render_failed', 'local_render_ready'].includes(version.status || '')
    );
    if (versions.length && (p.current_version_id || pendingVersion)) {
      const cur = versions.find((v) => v.id === p.current_version_id) || pendingVersion || versions[0];
      store.setCurrentVersionId(cur.id);
      store.setVersion(`v${cur.version_number}.0`);
      store.setPreviewUrl(null);
      store.setStep('result');
      store.setTaskText(
        cur.status === 'local_render_failed'
          ? '上一版严格验收发现可能问题，等待你决定是否重试'
          : pendingVersion
            ? '工程已就绪，可在当前浏览器生成 MP4'
            : '生成完成',
      );
    } else {
      store.setCurrentVersionId(null);
      store.setPreviewUrl(null);
      store.setPendingPatch(null);
      store.setVersion('v1.0');
      store.setGenerateHyperFramesProgress(0);
      store.setGenerateDraftProgress(0);
      try {
        const plan = await api.getEditPlan(projectId);
        store.setEditPlan(plan);
        store.setStep('plan');
        store.setTaskText('分析完成，请确认剪辑方案');
      } catch {
        store.setEditPlan(null);
        store.setStep('upload');
        store.setTaskText('准备就绪');
      }
    }
    try {
      const history = await api.getChat(projectId);
      store.setChatMessages(history.map(mapChatMessage));
    } catch {
      /* ignore */
    }
  }, [refreshAssets, store]);

  const refreshVersions = useCallback(async (projectId: string, doneText?: string) => {
    const project = await api.getProject(projectId);
    const versions = await api.listVersions(projectId);
    store.setVersions(versions);
    const current = versions.find((v) => v.id === project.current_version_id) || versions[0];
    if (current) {
      store.setCurrentVersionId(current.id);
      store.setVersion(`v${current.version_number}.0`);
      store.setPreviewUrl(null);
      store.setStep('result');
    }
    if (doneText) store.setTaskText(doneText);
  }, [store]);

  const refreshChat = useCallback(async (projectId: string) => {
    const history = await api.getChat(projectId);
    store.setChatMessages(history.map(mapChatMessage));
  }, [store]);

  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      store.setError(null);
      const projectId = await ensureProject();
      for (const file of Array.from(files)) {
        const label =
          file.type.startsWith('audio')
            ? '音频'
            : file.type.startsWith('image')
              ? '图片'
              : /\.(txt|md|markdown)$/i.test(file.name)
                ? '讲稿'
                : '素材';
        await api.uploadAsset(projectId, file, label, '');
      }
      await refreshAssets(projectId);
    },
    [ensureProject, refreshAssets, store]
  );

  const watchJob = useCallback(
    (jobId: string, onDone?: (job: BackendJob) => void | Promise<void>, onTerminal?: (job: BackendJob) => void | Promise<void>) => {
      jobEsRef.current?.close();
      store.setActiveJobId(jobId);
      jobLogCountsRef.current[jobId] = jobLogCountsRef.current[jobId] ?? 0;
      jobEsRef.current = api.watchJob(jobId, (job) => {
        store.setTaskText(job.current_step || '处理中');
        const seenLogs = jobLogCountsRef.current[job.id] ?? 0;
        const logs = job.logs || [];
        if (logs.length > seenLogs) {
          logs.slice(seenLogs).forEach((line, idx) => {
            const text = String(line || '').trim();
            if (!text) return;
            store.addChatMessage({
              id: `${job.id}-log-${seenLogs + idx}`,
              role: 'agent',
              text,
              timestamp: Date.now(),
            });
          });
          jobLogCountsRef.current[job.id] = logs.length;
        }
        if (job.type === 'analyze' || store.step === 'analyze') {
          store.setOverallProgress(Math.round(job.progress));
          store.setCurrentAnalyzeTask(job.current_step || '处理中');
          if (job.completed_steps) store.setAnalyzeCompletedSteps(job.completed_steps);
          if (job.logs) store.setAnalyzeLogs(job.logs);
          if (job.warnings?.length) store.setJobWarnings(job.warnings);
          if (typeof job.plan_progress === 'number') store.setPlanProgress(job.plan_progress);
          store.setPlanSubstep(job.plan_substep ?? null);
        }
        if (job.type === 'generate' || store.step === 'generate') {
          const p = Math.round(job.progress);
          store.setGenerateHyperFramesProgress(Math.min(100, p));
          store.setGenerateDraftProgress(Math.min(100, Math.max(0, p - 10)));
        }
        if (job.status === 'failed') {
          store.setError(job.error_message || '任务失败');
        }
        if (job.status === 'needs_input') {
          store.setTaskText(job.current_step || '需要用户补充信息');
          store.setError(null);
        }
        if (job.status === 'completed') {
          void Promise.resolve(onDone?.(job)).catch((e) => {
            store.setError(e instanceof Error ? e.message : '任务完成后的刷新失败');
          });
        }
        if (TERMINAL_JOB_STATUSES.includes(job.status)) {
          store.setActiveJobId(null);
          void Promise.resolve(onTerminal?.(job)).catch(() => undefined);
        }
      });
    },
    [store]
  );

  const loadProjectWithActiveJob = useCallback(async (projectId: string) => {
    await loadProject(projectId);
    const active = await api.getActiveJob(projectId).catch(() => null);
    if (!active || TERMINAL_JOB_STATUSES.includes(active.status)) return;

    if (active.type === 'analyze') {
      store.setStep('analyze');
      store.setOverallProgress(Math.round(active.progress));
    } else if (active.type === 'generate' || active.type === 'apply_patch') {
      store.setStep('generate');
      store.setGenerateHyperFramesProgress(Math.round(active.progress));
      store.setGenerateDraftProgress(Math.max(0, Math.round(active.progress) - 10));
    } else if (active.type === 'chat') {
      store.setChatBusy(true);
    }

    watchJob(
      active.id,
      async (job) => {
        if (job.type === 'analyze') {
          const plan = await api.getEditPlan(projectId);
          store.setEditPlan(plan);
          store.setStep('plan');
          store.setTaskText('分析完成，请确认剪辑方案');
          await refreshAssets(projectId);
        } else if (job.type === 'chat') {
          await refreshChat(projectId);
          await refreshVersions(projectId, '对话处理完成');
        } else {
          await refreshVersions(projectId, '生成完成');
        }
      },
      async (job) => {
        if (job.type === 'chat') store.setChatBusy(false);
      },
    );
  }, [loadProject, refreshAssets, refreshChat, refreshVersions, store, watchJob]);

  const startAnalyze = useCallback(async () => {
    store.setError(null);
    if (!store.projectId) {
      store.setError('当前工作台没有绑定项目，请返回新建项目后再试');
      return;
    }
    if (store.inputMode === 'topic' && !store.topic.trim()) {
      store.setError('请先填写科普主题');
      return;
    }
    if (store.inputMode === 'script' && !store.scriptText.trim()) {
      store.setError('请先填写完整科普文案');
      return;
    }
    if (store.inputMode === 'media' && store.assets.length === 0) {
      store.setError('请先上传一条视频或音频');
      return;
    }
    try {
      const projectId = store.projectId;
      if (store.inputMode === 'script' && store.scriptText.trim()) {
        await api.putScript(projectId, store.scriptText);
      }
      store.setTaskText('正在启动 Agent 分析');
      store.setStep('analyze');
      store.setOverallProgress(1);
      store.setAnalyzeCompletedSteps([]);
      store.setAnalyzeLogs([]);
      store.setJobWarnings([]);
      store.setPlanProgress(0);
      store.setPlanSubstep(null);
      const job = await api.analyze(projectId);
      watchJob(
        job.id,
        async () => {
          const plan = await api.getEditPlan(projectId);
          store.setEditPlan(plan);
          store.setStep('plan');
          store.setTaskText('分析完成，请确认视频方案');
          await refreshAssets(projectId);
        },
        async (terminalJob) => {
          if (terminalJob.status === 'needs_input' || terminalJob.status === 'failed') {
            await refreshChat(projectId);
          }
        },
      );
    } catch (error) {
      store.setStep('upload');
      store.setTaskText('Agent 分析未启动');
      store.setError(error instanceof Error ? error.message : '启动科普方案失败，请稍后重试');
    }
  }, [refreshAssets, refreshChat, store, watchJob]);

  const persistAsset = useCallback(async (assetId: string, body: Record<string, unknown>) => {
    await api.updateAsset(assetId, body);
    if (store.projectId) await refreshAssets(store.projectId);
  }, [refreshAssets, store]);

  const finishLocalRender = useCallback(async function completeLocalRender(
    projectId: string,
    versionId: string,
    bundleUrl: string,
    fps: number,
    autoRepairAttempt = 0,
  ) {
    try {
      store.setTaskText('正在浏览器内准备 HyperFrames 工程');
      store.setGenerateHyperFramesProgress(86);
      const bundle = await api.downloadLocalRenderBundle(bundleUrl);
      const localResult = await renderLocally(bundle, fps, (progress, step) => {
        store.setTaskText(step);
        store.setGenerateHyperFramesProgress(Math.min(98, 86 + Math.round(progress * 0.12)));
      });
      store.setTaskText('正在提交逐幕联系表进行视觉验收');
      store.setGenerateHyperFramesProgress(99);
      const review = await api.reviewLocalRender(
        projectId,
        versionId,
        localResult.contactSheet,
        localResult.mediaValidation,
      );
      const videoUrl = URL.createObjectURL(localResult.video);
      if (review.status === 'awaiting_user_retry') {
        store.setGenerateHyperFramesProgress(100);
        await refreshVersions(projectId, '成片已生成，严格验收发现可能问题，等待你决定是否重试');
        store.setPreviewUrl(videoUrl);
        store.setStep('result');
        await refreshChat(projectId);
        return;
      }
      store.setGenerateHyperFramesProgress(100);
      await refreshVersions(projectId, '浏览器渲染与视觉验收完成，MP4 仅保留在当前设备');
      store.setPreviewUrl(videoUrl);
    } catch (error) {
      if (!isBrowserEnvironmentError(error) && autoRepairAttempt < 3) {
        const message = error instanceof Error ? error.message : '本机没有生成可播放视频';
        const nextAttempt = autoRepairAttempt + 1;
        store.setStep('generate');
        store.setTaskText(`本机未生成可播放视频，正在自动修复工程 ${nextAttempt}/3`);
        store.addChatMessage({
          id: newClientId(),
          role: 'agent',
          text: `本机没有生成可播放视频，正在自动进行第 ${nextAttempt}/3 次工程修复。原因：${message}`,
          timestamp: Date.now(),
        });
        const repairJob = await api.reportLocalRenderFailure(projectId, versionId, message, nextAttempt);
        watchJob(
          repairJob.id,
          async (completedJob) => {
            const result = completedJob.result;
            if (result?.render_target === 'local' && result.version_id && result.bundle_url) {
              await completeLocalRender(projectId, result.version_id, result.bundle_url, result.fps || fps, nextAttempt);
              return;
            }
            await refreshVersions(projectId, '自动修复工程已完成');
          },
          async () => {
            await refreshChat(projectId);
          },
        );
        return;
      }
      throw error;
    }
  }, [refreshChat, refreshVersions, store, watchJob]);

  const retryFailedRender = useCallback(async (versionId: string) => {
    const projectId = store.projectId;
    if (!projectId || store.activeJobId) return;
    store.setError(null);
    store.setChatBusy(true);
    store.setStep('generate');
    store.setGenerateHyperFramesProgress(2);
    store.setGenerateDraftProgress(0);
    store.setTaskText('正在把验收问题反馈给提示词 AI');
    try {
      const job = await api.retryLocalRender(projectId, versionId);
      await refreshChat(projectId);
      watchJob(
        job.id,
        async (completedJob) => {
          const result = completedJob.result;
          if (result?.render_target === 'local' && result.version_id && result.bundle_url) {
            await finishLocalRender(projectId, result.version_id, result.bundle_url, result.fps || store.frameRate);
            return;
          }
          await refreshVersions(projectId, '重试工程已完成');
        },
        async () => {
          store.setChatBusy(false);
          await refreshChat(projectId);
        },
      );
    } catch (error) {
      store.setChatBusy(false);
      store.setStep('result');
      store.setError(error instanceof Error ? error.message : '重试任务未能启动');
    }
  }, [finishLocalRender, refreshChat, refreshVersions, store, watchJob]);

  const runChatAction = useCallback(async (action: string, versionId: string, messageId: string) => {
    const projectId = store.projectId;
    if (!projectId || store.activeJobId) return;
    const isFineTune = action === 'fine_tune_video';
    const isRegenerate = action === 'regenerate_video';
    if (!isFineTune && !isRegenerate) return;
    store.setError(null);
    store.setChatBusy(true);
    store.setStep('generate');
    store.setGenerateHyperFramesProgress(2);
    store.setGenerateDraftProgress(0);
    store.setTaskText(isFineTune ? '对话 AI 正在微调当前工程' : '提示词 AI 正在重新规划全片');
    try {
      const job = isFineTune
        ? await api.fineTuneFromChat(projectId, versionId, messageId)
        : await api.regenerateFromChat(projectId, versionId, messageId);
      await refreshChat(projectId);
      watchJob(
        job.id,
        async (completedJob) => {
          const result = completedJob.result;
          if (result?.render_target === 'local' && result.version_id && result.bundle_url) {
            await finishLocalRender(projectId, result.version_id, result.bundle_url, result.fps || store.frameRate);
            return;
          }
          await refreshVersions(projectId, isFineTune ? '微调工程已完成' : '重新生成工程已完成');
        },
        async () => {
          store.setChatBusy(false);
          await refreshChat(projectId);
        },
      );
    } catch (error) {
      store.setChatBusy(false);
      store.setStep('result');
      store.setError(error instanceof Error ? error.message : '对话动作未能启动');
    }
  }, [finishLocalRender, refreshChat, refreshVersions, store, watchJob]);

  const startGenerate = useCallback(async () => {
    const projectId = store.projectId;
    if (!projectId) return;
    if (store.scriptText.trim()) {
      await api.putScript(projectId, store.scriptText);
    }
    store.setError(null);
    store.setStep('generate');
    store.setGenerateHyperFramesProgress(0);
    store.setGenerateDraftProgress(0);
    const resolution = store.videoResolution.startsWith('720')
      ? '720p'
      : store.videoResolution.startsWith('4K')
        ? '4K旗舰版'
        : '1080p';
    const pendingVersions = await api.listVersions(projectId);
    const failed = pendingVersions.find((version) => version.status === 'local_render_failed');
    if (failed) {
      store.setCurrentVersionId(failed.id);
      store.setStep('result');
      store.setTaskText('上一版严格验收发现可能问题，请在 Agent 对话中决定是否重试');
      await refreshChat(projectId);
      return;
    }
    const pending = pendingVersions.find((version) =>
      ['awaiting_local_render', 'local_render_ready'].includes(version.status || '')
    );
    if (pending?.local_render_bundle_url) {
      try {
        await finishLocalRender(
          projectId,
          pending.id,
          pending.local_render_bundle_url,
          pending.render_fps || store.frameRate,
        );
      } catch (error) {
        store.setError(error instanceof Error ? error.message : '浏览器渲染未完成');
        store.setTaskText('浏览器渲染需要重试');
      }
      return;
    }

    try {
      await checkLocalRenderer();
    } catch (error) {
      store.setError(error instanceof Error ? error.message : '当前浏览器不支持本地渲染');
      store.setTaskText('当前浏览器无法编码 MP4');
      return;
    }

    const job = await api.generate(projectId, { resolution, fps: store.frameRate, render_target: 'local' });
    watchJob(job.id, async (completedJob) => {
      const result = completedJob.result;
      if (result?.render_target === 'local' && result.version_id && result.bundle_url) {
        try {
          await finishLocalRender(
            projectId,
            result.version_id,
            result.bundle_url,
            result.fps || store.frameRate,
          );
        } catch (error) {
          store.setError(error instanceof Error ? error.message : '浏览器渲染未完成');
          store.setTaskText('浏览器渲染需要重试');
          store.addChatMessage({
            id: newClientId(),
            role: 'agent',
            text: '视频工程已经完成，但当前浏览器没有完成本地 MP4 编码。使用最新版 Chrome 或 Edge 再次点击生成即可继续，不会重新调用 Agent。',
            timestamp: Date.now(),
          });
        }
        return;
      }
      await refreshVersions(projectId, '生成完成');
    });
  }, [finishLocalRender, refreshChat, refreshVersions, store, watchJob]);

  // 发送消息给当前项目自己的 Agent：可问答，也可直接改片并生成新版本。
  const sendChat = useCallback(
    async (message: string) => {
      const text = message.trim();
      if (!text) return;
      if (store.activeJobId) {
        store.addChatMessage({
          id: newClientId(),
          role: 'agent',
          text: '当前项目已有 agent 任务在运行，请等它完成后再发送新的修改。',
          timestamp: Date.now(),
        });
        return;
      }
      store.setChatBusy(true);
      store.setTaskText('正在准备对话 AI');
      store.addChatMessage({ id: newClientId(), role: 'user', text, timestamp: Date.now() });
      store.addChatMessage({
        id: newClientId(),
        role: 'agent',
        text: '已收到你的消息，正在读取当前项目工程并判断修改方式。',
        timestamp: Date.now(),
      });
      try {
        const projectId = store.projectId;
        if (!projectId) {
          throw new Error('当前工作台没有绑定项目，请先新建项目');
        }
        store.setTaskText('对话 AI 正在处理消息');
        const res = await api.chat(projectId, text, true);
        store.addChatMessage({
          id: res.id,
          role: 'agent',
          text: res.content,
          timestamp: Date.now(),
          patch: res.patch,
          action: res.action,
          versionId: res.version_id,
        });
        if (res.status === 'proposed' && res.patch) {
          store.setPendingPatch(res.patch);
        } else {
          store.setPendingPatch(null);
        }
        if (res.job_id) {
          watchJob(
            res.job_id,
            async (job) => {
              await refreshChat(projectId);
              const result = job.result;
              if (result?.render_target === 'local' && result.version_id && result.bundle_url) {
                try {
                  store.setStep('generate');
                  await finishLocalRender(projectId, result.version_id, result.bundle_url, result.fps || store.frameRate);
                } catch (error) {
                  store.setError(error instanceof Error ? error.message : '浏览器渲染未完成');
                  store.setTaskText('浏览器渲染需要重试');
                }
                return;
              }
              await refreshVersions(projectId, '对话处理完成');
            },
            async (job) => {
              store.setChatBusy(false);
              if (job.status === 'failed' || job.status === 'needs_input') {
                await refreshChat(projectId).catch(() => {
                  if (job.status === 'failed') {
                    store.addChatMessage({
                      id: newClientId(),
                      role: 'agent',
                      text: `对话任务未完成：${job.error_message || '请查看上方 Agent 日志。'}`,
                      timestamp: Date.now(),
                    });
                  }
                });
              }
            },
          );
        } else {
          store.setChatBusy(false);
        }
      } catch (e) {
        store.setChatBusy(false);
        const msg = e instanceof Error ? e.message : '未知错误';
        store.setError(msg);
        store.addChatMessage({
          id: newClientId(),
          role: 'agent',
          text: `这条消息未能提交：${msg}\n\n消息已保留在对话中，你可以稍后重试。`,
          timestamp: Date.now(),
        });
      }
    },
    [finishLocalRender, refreshChat, refreshVersions, store, watchJob]
  );

  const saveScriptText = useCallback(async (text: string) => {
    store.setScriptText(text);
    if (store.projectId) {
      await api.putScript(store.projectId, text);
    }
  }, [store]);

  // 接受修改方案：应用 patch 并重新生成
  const acceptPatch = useCallback(async () => {
    const projectId = store.projectId;
    const patch = store.pendingPatch;
    if (!projectId || !patch) return;
    store.setPendingPatch(null);
    store.setStep('generate');
    store.setGenerateHyperFramesProgress(0);
    store.setGenerateDraftProgress(0);
    const job = await api.applyPatch(projectId, patch);
    store.addChatMessage({ id: newClientId(), role: 'agent', text: '已接受修改，正在重新生成科普视频预览…', timestamp: Date.now() });
    watchJob(job.id, async (completedJob) => {
      const result = completedJob.result;
      if (result?.render_target === 'local' && result.version_id && result.bundle_url) {
        try {
          await finishLocalRender(projectId, result.version_id, result.bundle_url, result.fps || store.frameRate);
        } catch (error) {
          store.setError(error instanceof Error ? error.message : '浏览器渲染未完成');
          store.setTaskText('浏览器渲染需要重试');
        }
        return;
      }
      await refreshVersions(projectId, '修改已应用并重新生成');
    });
  }, [finishLocalRender, refreshVersions, store, watchJob]);

  // 撤销/放弃当前修改方案
  const discardPatch = useCallback(() => {
    store.setPendingPatch(null);
    store.addChatMessage({ id: newClientId(), role: 'agent', text: '已撤销该修改方案，未做任何改动。', timestamp: Date.now() });
  }, [store]);

  // 回退到上一个版本（撤销已应用的修改）
  const revertToPreviousVersion = useCallback(async () => {
    const projectId = store.projectId;
    const versions = store.versions;
    if (!projectId || versions.length < 2) return;
    const currentIdx = versions.findIndex((v) => v.id === store.currentVersionId);
    const target = versions[currentIdx + 1] || versions[1];
    if (!target) return;
    await api.activateVersion(projectId, target.id);
    store.setCurrentVersionId(target.id);
    store.setVersion(`v${target.version_number}.0`);
    store.setPreviewUrl(null);
    store.addChatMessage({ id: newClientId(), role: 'agent', text: `已撤销到上一版本 v${target.version_number}.0。`, timestamp: Date.now() });
  }, [store]);

  return {
    ensureProject,
    loadProject: loadProjectWithActiveJob,
    uploadFiles,
    startAnalyze,
    startGenerate,
    sendChat,
    runChatAction,
    acceptPatch,
    discardPatch,
    revertToPreviousVersion,
    retryFailedRender,
    saveScriptText,
    refreshAssets,
    persistAsset,
  };
}
