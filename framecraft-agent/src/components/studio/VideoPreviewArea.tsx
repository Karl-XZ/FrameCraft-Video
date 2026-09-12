import React from 'react';
import { useProjectStore } from '../../store/projectStore';

export default function VideoPreviewArea() {
  const { previewUrl, videoRatio } = useProjectStore();
  const aspect = videoRatio === '16:9' ? 'aspect-video' : videoRatio === '1:1' ? 'aspect-square' : 'aspect-[9/16]';

  if (!previewUrl) {
    return (
      <div className={`relative rounded-2xl overflow-hidden bg-black/80 ${aspect} flex items-center justify-center`}>
        <p className="text-xs text-text-muted">预览视频生成后将显示在这里</p>
      </div>
    );
  }

  return (
    <div className={`relative rounded-2xl overflow-hidden bg-black ${aspect} max-h-full`}>
      <video src={previewUrl} controls className="w-full h-full object-contain" />
    </div>
  );
}
