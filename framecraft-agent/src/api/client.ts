const viteBase = import.meta.env.BASE_URL === '/' ? '' : import.meta.env.BASE_URL.replace(/\/$/, '');
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? viteBase;
async function authFetch(input: string, options?: RequestInit): Promise<Response> {
  return fetch(input, options);
}

function urlWithAccessToken(path: string) {
  return `${API_BASE}${path}`;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await authFetch(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) return res.json();
  return undefined as T;
}

export interface BackendProject {
  id: string;
  agent_session_id?: string;
  name: string;
  status: string;
  aspect_ratio: string;
  target_style: string;
  target_duration: number;
  input_mode?: 'topic' | 'script' | 'media';
  topic?: string;
  requirements?: string;
  output_language?: string;
  script_text?: string;
  generate_draft?: boolean;
  keep_hyperframes?: boolean;
  current_version_id: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface BackendAsset {
  id: string;
  project_id: string;
  file_name: string;
  file_type: string;
  mime_type: string;
  size: number;
  duration: number | null;
  user_label: string;
  user_note: string;
  must_use: boolean;
  priority: number;
  analysis_status: string;
  thumbnail_url: string | null;
}

export interface BackendJob {
  id: string;
  project_id: string;
  type: string;
  status: string;
  progress: number;
  current_step: string;
  error_message: string | null;
  completed_steps?: string[];
  plan_substep?: string | null;
  plan_progress?: number;
  logs?: string[];
  warnings?: Array<{ code?: string; message: string; asset_id?: string }>;
  result?: {
    render_target?: string;
    version_id?: string;
    bundle_url?: string;
    fps?: number;
    expected_duration_s?: number;
  };
}

export interface BackendChatMessage {
  id: string;
  role: string;
  content: string;
  created_at: string;
  patch?: Record<string, unknown>;
  status?: string;
  action?: string | null;
  version_id?: string;
  action_payload?: Record<string, unknown>;
}

export interface ModelProviderMeta {
  id: string;
  label: string;
  base_url?: string;
  note?: string;
}

export interface AssetAnalysis {
  ready: boolean;
  asset_id: string;
  auto_summary?: string;
  recommended_usage?: string[];
  ocr_text?: string;
  vision_status?: string;
  vision_error?: string | null;
  ocr_status?: string;
  ocr_error?: string | null;
  meta?: Record<string, unknown>;
  frame_urls?: string[];
  broll_segments?: Array<{ time?: number; text?: string; source?: string; asset_id?: string }>;
}

export interface EditPlan {
  video_concept: string;
  target_duration: number;
  style: string;
  hook: string;
  subtitle_style: string;
  bgm_note: string;
  scenes: Array<Record<string, unknown>>;
  broll_plan: Array<{ time: number; text: string; source: string; asset_id?: string }>;
  meta?: {
    llm_status?: string;
    llm_error?: string | null;
    llm_note?: string | null;
  };
}

export interface BackendVersion {
  id: string;
  version_number: number;
  preview_url: string | null;
  draft_url: string | null;
  timeline_url: string | null;
  subtitles_url: string | null;
  source_ledger_url?: string | null;
  cover_url: string | null;
  publish_copy_url: string | null;
  hyperframes_url: string | null;
  status?: string;
  local_render_bundle_url?: string | null;
  render_fps?: number;
  expected_duration_s?: number;
}

export interface LocalRenderReviewResult {
  ok?: boolean;
  status?: string;
  review?: Record<string, unknown>;
  revision_scene_numbers?: number[];
  job?: BackendJob;
  id?: string;
  version_id?: string;
}

export interface CreateProjectBody {
  name: string;
  aspect_ratio?: string;
  target_duration?: number;
  target_style?: string;
  output_language?: string;
  script_text?: string;
  input_mode?: 'topic' | 'script' | 'media';
  topic?: string;
  requirements?: string;
  generate_draft?: boolean;
  keep_hyperframes?: boolean;
}

export const api = {
  base: API_BASE,
  createProject: (body: CreateProjectBody) =>
    request<BackendProject>('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: body.name,
        aspect_ratio: body.aspect_ratio || '9:16',
        target_duration: body.target_duration || 60,
        target_style: body.target_style || 'science_explainer',
        input_mode: body.input_mode || 'topic',
        topic: body.topic || '',
        requirements: body.requirements || '',
        script_text: body.script_text || '',
        output_language: body.output_language || 'zh',
        generate_draft: body.generate_draft ?? false,
        keep_hyperframes: body.keep_hyperframes ?? true,
      }),
    }),
  getProject: (projectId: string) => request<BackendProject>(`/api/projects/${projectId}`),
  deleteProject: (projectId: string) => request(`/api/projects/${projectId}`, { method: 'DELETE' }),
  listAssets: (projectId: string) => request<BackendAsset[]>(`/api/projects/${projectId}/assets`),
  getScript: (projectId: string) => request<{ text: string }>(`/api/projects/${projectId}/script`),
  putScript: (projectId: string, text: string) =>
    request<BackendProject>(`/api/projects/${projectId}/script`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),
  uploadAsset: async (projectId: string, file: File, user_label = '', user_note = '') => {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('user_label', user_label);
    fd.append('user_note', user_note);
    const res = await authFetch(`${API_BASE}/api/projects/${projectId}/assets/upload`, { method: 'POST', body: fd });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<BackendAsset>;
  },
  updateAsset: (assetId: string, body: Record<string, unknown>) =>
    request(`/api/assets/${assetId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  getAssetAnalysis: (assetId: string) => request<AssetAnalysis>(`/api/assets/${assetId}/analysis`),
  analyze: (projectId: string, opts?: { strategy?: string; platform?: string }) =>
    request<BackendJob>(`/api/projects/${projectId}/assets/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts || {}),
    }),
  getEditPlan: (projectId: string) => request<EditPlan>(`/api/projects/${projectId}/edit-plan`),
  generate: (projectId: string, opts?: { resolution?: string; fps?: number; strategy?: string; render_target?: string }) =>
    request<BackendJob>(`/api/projects/${projectId}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts || {}),
    }),
  downloadLocalRenderBundle: async (path: string) => {
    const response = await authFetch(`${API_BASE}${path}`);
    if (!response.ok) throw new Error(await response.text());
    return response.blob();
  },
  reviewLocalRender: async (
    projectId: string,
    versionId: string,
    contactSheet: Blob,
    mediaValidation: Record<string, unknown>,
  ) => {
    const form = new FormData();
    form.append('file', contactSheet, 'contact-sheet.jpg');
    form.append('media_json', JSON.stringify(mediaValidation));
    const response = await authFetch(
      `${API_BASE}/api/projects/${projectId}/versions/${versionId}/local-render-review`,
      { method: 'POST', body: form },
    );
    if (!response.ok) throw new Error(await response.text());
    return response.json() as Promise<LocalRenderReviewResult>;
  },
  retryLocalRender: (projectId: string, versionId: string) =>
    request<BackendJob>(`/api/projects/${projectId}/versions/${versionId}/retry`, { method: 'POST' }),
  regenerateFromChat: (projectId: string, versionId: string, messageId?: string) =>
    request<BackendJob>(`/api/projects/${projectId}/versions/${versionId}/regenerate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message_id: messageId || null }),
    }),
  fineTuneFromChat: (projectId: string, versionId: string, messageId?: string) =>
    request<BackendJob>(`/api/projects/${projectId}/versions/${versionId}/fine-tune`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message_id: messageId || null }),
    }),
  reportLocalRenderFailure: (
    projectId: string,
    versionId: string,
    error: string,
    attempt: number,
    mediaValidation: Record<string, unknown> = {},
  ) =>
    request<BackendJob>(`/api/projects/${projectId}/versions/${versionId}/local-render-failed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ error, attempt, media_validation: mediaValidation }),
    }),
  applyPatch: (projectId: string, patch: Record<string, unknown>) =>
    request<BackendJob>(`/api/projects/${projectId}/apply-patch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patch }),
    }),
  cancelJob: (jobId: string) => request(`/api/jobs/${jobId}/cancel`, { method: 'POST' }),
  getImportGuide: (projectId: string, versionId: string) =>
    request<{ content: string }>(`/api/projects/${projectId}/versions/${versionId}/import-guide`),
  getJob: (jobId: string) => request<BackendJob>(`/api/jobs/${jobId}`),
  getActiveJob: (projectId: string) => request<BackendJob | null>(`/api/projects/${projectId}/jobs/active`),
  watchJob: (jobId: string, onEvent: (job: BackendJob) => void) => {
    const es = new EventSource(urlWithAccessToken(`/api/jobs/${jobId}/events`));
    es.onmessage = (ev) => {
      const data = JSON.parse(ev.data) as BackendJob;
      onEvent(data);
      if (['completed', 'failed', 'cancelled'].includes(data.status)) es.close();
    };
    return es;
  },
  listVersions: (projectId: string) => request<BackendVersion[]>(`/api/projects/${projectId}/versions`),
  chat: (projectId: string, message: string, apply = true) =>
    request<{
      id: string;
      role: string;
      content: string;
      patch?: Record<string, unknown>;
      job_id?: string;
      status?: string;
      action?: string | null;
      version_id?: string;
    }>(
      `/api/projects/${projectId}/chat`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message, apply }) }
    ),
  activateVersion: (projectId: string, versionId: string) =>
    request<{ ok: boolean; current_version_id: string }>(
      `/api/projects/${projectId}/versions/${versionId}/activate`,
      { method: 'POST' }
    ),
  getChat: (projectId: string) =>
    request<BackendChatMessage[]>(`/api/projects/${projectId}/chat`),
  fileUrl: (path: string) => urlWithAccessToken(path),
};

export function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDuration(sec: number | null | undefined) {
  if (!sec) return undefined;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function mapAssetType(label: string, fileType: string): import('../store/projectStore').Asset['type'] {
  if (fileType === 'audio' || label === '音频') return '音频';
  if (/\.(txt|md|markdown)$/i.test(label) || label === '讲稿') return '讲稿';
  if (fileType === 'image') return '图片';
  return '素材';
}
