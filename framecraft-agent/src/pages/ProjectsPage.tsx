import React, { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Zap, Plus, Film, Trash2, ArrowLeft } from 'lucide-react';
import { api, type BackendProject } from '../api/client';

const STATUS_LABEL: Record<string, string> = {
  created: '已创建',
  uploading: '上传中',
  analyzing: '分析中',
  planning: '规划中',
  rendering: '渲染中',
  chatting: 'Agent 对话中',
  completed: '已完成',
  awaiting_local_render: '工程就绪',
  awaiting_retry_decision: '等待重试确认',
  ready_to_render: '可本地渲染',
  needs_input: '等待补充',
  failed: '失败',
  cancelled: '已取消',
};

export default function ProjectsPage() {
  const [projects, setProjects] = useState<BackendProject[]>([]);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const load = async () => {
    setLoading(true);
    try {
      setProjects(await api.listProjects());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!window.confirm('确定删除该项目及其素材？此操作不可恢复。')) return;
    await api.deleteProject(id);
    await load();
  };

  return (
    <div className="min-h-screen bg-bg-main relative overflow-x-hidden">
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute top-0 left-0 w-[600px] h-[600px] rounded-full animate-orb-1"
          style={{ background: 'radial-gradient(circle, rgba(124,58,237,0.28), transparent 30%)' }} />
        <div className="absolute top-0 right-0 w-[700px] h-[700px] rounded-full animate-orb-2"
          style={{ background: 'radial-gradient(circle, rgba(6,182,212,0.2), transparent 35%)' }} />
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

      <main className="relative z-10 max-w-screen-lg mx-auto w-full px-8 py-6">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-3xl font-extrabold text-text-main">项目列表</h1>
            <p className="text-sm text-text-muted mt-1">公共工作区中的全部项目</p>
          </div>
          <Link to="/projects/new" className="gradient-btn px-5 py-3 rounded-xl text-sm font-bold flex items-center gap-2 shadow-glow">
            <Plus className="w-4 h-4" /> 新建项目
          </Link>
        </div>

        {loading ? (
          <p className="text-sm text-text-muted">加载中…</p>
        ) : projects.length === 0 ? (
          <div className="glass-card rounded-2xl p-12 text-center">
            <Film className="w-10 h-10 text-text-muted mx-auto mb-4" />
            <p className="text-sm text-text-secondary mb-4">还没有项目，立即创建第一个吧</p>
            <Link to="/projects/new" className="gradient-btn inline-flex px-5 py-2.5 rounded-xl text-sm font-bold items-center gap-2">
              <Plus className="w-4 h-4" /> 新建项目
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {projects.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => navigate(`/studio?project=${p.id}`)}
                className="glass-card rounded-2xl p-5 text-left hover:bg-white/[0.06] transition-colors group"
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-primary/15 flex items-center justify-center">
                      <Film className="w-5 h-5 text-primary-light" />
                    </div>
                    <div>
                      <p className="text-sm font-bold text-text-main">{p.name}</p>
                      <p className="text-xs text-text-muted mt-0.5">{p.aspect_ratio} · {p.target_duration}s · {p.target_style}</p>
                    </div>
                  </div>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => void handleDelete(p.id, e)}
                    className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-warning transition-all p-1.5 rounded-lg hover:bg-white/[0.06]"
                  >
                    <Trash2 className="w-4 h-4" />
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-4">
                  <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-white/5 border border-white/10 text-text-secondary">
                    {STATUS_LABEL[p.status] || p.status}
                  </span>
                  {p.current_version_id && (
                    <span className="px-2.5 py-1 rounded-full text-xs font-medium bg-secondary/10 border border-secondary/20 text-secondary">
                      已有成片
                    </span>
                  )}
                </div>
              </button>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
