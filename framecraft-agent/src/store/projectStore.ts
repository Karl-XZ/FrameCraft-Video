import { create } from 'zustand';
import type { BackendVersion, EditPlan } from '../api/client';

export type Step = 'upload' | 'analyze' | 'plan' | 'generate' | 'result';
export type AssetType = 'all' | '音频' | '讲稿' | '图片' | '素材';

export interface Asset {
  id: string;
  filename: string;
  type: '音频' | '讲稿' | '图片' | '素材';
  duration?: string;
  size: string;
  note: string;
  status: '已转写' | '待分析' | '已上传' | '分析完成';
  thumbnail?: string;
  mustUse: boolean;
  priority: number;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'agent';
  text: string;
  timestamp: number;
  patch?: Record<string, unknown>;
  action?: string | null;
  versionId?: string;
}

interface ProjectState {
  projectId: string | null;
  step: Step;
  demoMode: boolean;
  assets: Asset[];
  filter: AssetType;
  chatMessages: ChatMessage[];
  showAssetDrawer: boolean;
  selectedAssetId: string | null;
  videoRatio: string;
  videoResolution: string;
  frameRate: number;
  targetDuration: number;
  targetStyle: string;
  inputMode: 'topic' | 'script' | 'media';
  topic: string;
  requirements: string;
  outputLanguage: string;
  scriptText: string;
  generateDraft: boolean;
  keepHyperframes: boolean;
  draftTarget: string;
  overallProgress: number;
  currentAnalyzeTask: string;
  analyzeCompletedSteps: string[];
  analyzeLogs: string[];
  jobWarnings: Array<{ code?: string; message: string; asset_id?: string }>;
  planProgress: number;
  planSubstep: string | null;
  generateHyperFramesProgress: number;
  generateDraftProgress: number;
  taskText: string;
  version: string;
  currentVersionId: string | null;
  versions: BackendVersion[];
  editPlan: EditPlan | null;
  pendingPatch: Record<string, unknown> | null;
  previewUrl: string | null;
  error: string | null;
  chatBusy: boolean;
  activeJobId: string | null;

  setProjectId: (id: string | null) => void;
  setStep: (step: Step) => void;
  setDemoMode: (v: boolean) => void;
  setFilter: (f: AssetType) => void;
  setAssets: (assets: Asset[]) => void;
  addAsset: (asset: Asset) => void;
  removeAsset: (id: string) => void;
  setChatMessages: (msgs: ChatMessage[]) => void;
  addChatMessage: (msg: ChatMessage) => void;
  setShowAssetDrawer: (v: boolean) => void;
  setSelectedAssetId: (id: string | null) => void;
  setVideoRatio: (v: string) => void;
  setVideoResolution: (v: string) => void;
  setFrameRate: (v: number) => void;
  setTargetDuration: (v: number) => void;
  setTargetStyle: (v: string) => void;
  setInputMode: (v: 'topic' | 'script' | 'media') => void;
  setTopic: (v: string) => void;
  setRequirements: (v: string) => void;
  setOutputLanguage: (v: string) => void;
  setScriptText: (v: string) => void;
  setGenerateDraft: (v: boolean) => void;
  setKeepHyperframes: (v: boolean) => void;
  setDraftTarget: (v: string) => void;
  setOverallProgress: (v: number) => void;
  setCurrentAnalyzeTask: (v: string) => void;
  setAnalyzeCompletedSteps: (v: string[]) => void;
  setAnalyzeLogs: (v: string[]) => void;
  setJobWarnings: (v: Array<{ code?: string; message: string; asset_id?: string }>) => void;
  setPlanProgress: (v: number) => void;
  setPlanSubstep: (v: string | null) => void;
  setGenerateHyperFramesProgress: (v: number) => void;
  setGenerateDraftProgress: (v: number) => void;
  setTaskText: (v: string) => void;
  setVersion: (v: string) => void;
  setEditPlan: (p: EditPlan | null) => void;
  setVersions: (v: BackendVersion[]) => void;
  setCurrentVersionId: (id: string | null) => void;
  setPreviewUrl: (url: string | null) => void;
  setPendingPatch: (p: Record<string, unknown> | null) => void;
  setError: (e: string | null) => void;
  setChatBusy: (v: boolean) => void;
  setActiveJobId: (id: string | null) => void;
}

export const useProjectStore = create<ProjectState>((set) => ({
  projectId: null,
  step: 'upload',
  demoMode: false,
  assets: [],
  filter: 'all',
  chatMessages: [],
  showAssetDrawer: false,
  selectedAssetId: null,
  videoRatio: '9:16',
  videoResolution: '1080p正式导出',
  frameRate: 24,
  targetDuration: 60,
  targetStyle: 'science_explainer',
  inputMode: 'topic',
  topic: '',
  requirements: '',
  outputLanguage: 'zh',
  scriptText: '',
  generateDraft: false,
  keepHyperframes: true,
  draftTarget: '不导出草稿',
  overallProgress: 0,
  currentAnalyzeTask: '等待开始',
  analyzeCompletedSteps: [],
  analyzeLogs: [],
  jobWarnings: [] as Array<{ code?: string; message: string; asset_id?: string }>,
  planProgress: 0,
  planSubstep: null,
  generateHyperFramesProgress: 0,
  generateDraftProgress: 0,
  taskText: '准备就绪',
  version: 'v1.0',
  currentVersionId: null,
  versions: [],
  editPlan: null,
  pendingPatch: null,
  previewUrl: null,
  error: null,
  chatBusy: false,
  activeJobId: null,

  setProjectId: (id) => set({ projectId: id }),
  setStep: (step) => set({ step }),
  setDemoMode: (v) => set({ demoMode: v }),
  setFilter: (f) => set({ filter: f }),
  setAssets: (assets) => set({ assets }),
  addAsset: (asset) => set((s) => ({ assets: [...s.assets, asset] })),
  removeAsset: (id) => set((s) => ({ assets: s.assets.filter((a) => a.id !== id) })),
  setChatMessages: (msgs) => set({ chatMessages: msgs }),
  addChatMessage: (msg) => set((s) => ({ chatMessages: [...s.chatMessages, msg] })),
  setShowAssetDrawer: (v) => set({ showAssetDrawer: v }),
  setSelectedAssetId: (id) => set({ selectedAssetId: id }),
  setVideoRatio: (v) => set({ videoRatio: v }),
  setVideoResolution: (v) => set({ videoResolution: v }),
  setFrameRate: (v) => set({ frameRate: v }),
  setTargetDuration: (v) => set({ targetDuration: v }),
  setTargetStyle: (v) => set({ targetStyle: v }),
  setInputMode: (v) => set({ inputMode: v }),
  setTopic: (v) => set({ topic: v }),
  setRequirements: (v) => set({ requirements: v }),
  setOutputLanguage: (v) => set({ outputLanguage: v }),
  setScriptText: (v) => set({ scriptText: v }),
  setGenerateDraft: (v) => set({ generateDraft: v }),
  setKeepHyperframes: (v) => set({ keepHyperframes: v }),
  setDraftTarget: (v) => set({ draftTarget: v }),
  setOverallProgress: (v) => set({ overallProgress: v }),
  setCurrentAnalyzeTask: (v) => set({ currentAnalyzeTask: v }),
  setAnalyzeCompletedSteps: (v) => set({ analyzeCompletedSteps: v }),
  setAnalyzeLogs: (v) => set({ analyzeLogs: v }),
  setJobWarnings: (v) => set({ jobWarnings: v }),
  setPlanProgress: (v) => set({ planProgress: v }),
  setPlanSubstep: (v) => set({ planSubstep: v }),
  setGenerateHyperFramesProgress: (v) => set({ generateHyperFramesProgress: v }),
  setGenerateDraftProgress: (v) => set({ generateDraftProgress: v }),
  setTaskText: (v) => set({ taskText: v }),
  setVersion: (v) => set({ version: v }),
  setEditPlan: (p) => set({ editPlan: p }),
  setVersions: (v) => set({ versions: v }),
  setCurrentVersionId: (id) => set({ currentVersionId: id }),
  setPreviewUrl: (url) => set({ previewUrl: url }),
  setPendingPatch: (p) => set({ pendingPatch: p }),
  setError: (e) => set({ error: e }),
  setChatBusy: (v) => set({ chatBusy: v }),
  setActiveJobId: (id) => set({ activeJobId: id }),
}));
