import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import StudioLayout from '../components/layout/StudioLayout';
import { useProjectStore } from '../store/projectStore';
import { useStudioWorkflow } from '../hooks/useStudioWorkflow';

export default function StudioPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const { setError } = useProjectStore();
  const { loadProject } = useStudioWorkflow();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const projectId = params.get('project');
    if (!projectId) {
      navigate('/projects/new', { replace: true });
      return;
    }
    void loadProject(projectId)
      .catch((error) => {
        setError(error instanceof Error ? error.message : '项目加载失败');
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading) {
    return (
      <div className="h-screen bg-bg-main flex items-center justify-center text-sm text-text-muted">
        正在加载项目与 Agent 会话…
      </div>
    );
  }

  return (
    <div className="h-screen overflow-hidden">
      <StudioLayout />
    </div>
  );
}
