import React, { useEffect, useRef, useState } from 'react';
import { Send, Bot } from 'lucide-react';
import ChatMessageBubble from './ChatMessageBubble';
import AgentTypingIndicator from './AgentTypingIndicator';
import PatchConfirmCard from './PatchConfirmCard';
import QuickActionChips from './QuickActionChips';
import { useProjectStore } from '../../store/projectStore';
import { useStudioWorkflow } from '../../hooks/useStudioWorkflow';

const EMPTY_CHAT_HINT = `初版生成完成后，可以在这里与 Agent 对话修改视频。

初版前发送消息时，Agent 会提示先完成初版生成。`;

function actionLabel(action?: string | null) {
  if (action === 'retry_render') return '重试';
  if (action === 'regenerate_video') return '重新生成';
  if (action === 'fine_tune_video') return '微调';
  return undefined;
}

export default function AgentChatPanel() {
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const { projectId, chatMessages, pendingPatch, versions, currentVersionId, chatBusy } = useProjectStore();
  const { sendChat, acceptPatch, discardPatch, revertToPreviousVersion, retryFailedRender, runChatAction } = useStudioWorkflow();

  const canRevert = versions.length > 1 && versions.findIndex((v) => v.id === currentVersionId) < versions.length - 1;
  const busy = sending || chatBusy;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [chatMessages.length, busy, pendingPatch]);

  const submitMessage = async (text: string) => {
    const clean = text.trim();
    if (!clean || busy || !projectId) return;
    setSending(true);
    try {
      await sendChat(clean);
    } finally {
      setSending(false);
    }
  };

  const handleSend = async () => {
    const text = input;
    setInput('');
    await submitMessage(text);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/8">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-primary-light" />
          <span className="text-sm font-semibold text-text-main">Agent 对话</span>
          {busy && (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/15 text-primary-light border border-primary/25 animate-pulse">
              Agent 处理中
            </span>
          )}
        </div>
        {canRevert && (
          <button
            type="button"
            onClick={() => void revertToPreviousVersion()}
            className="text-xs text-text-muted hover:text-text-main transition-colors px-2 py-1 rounded-lg border border-white/10 hover:bg-white/[0.06]"
          >
            撤销到上一版本
          </button>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {chatMessages.length === 0 && !busy ? (
          <div className="h-full flex items-center justify-center">
            <p className="text-xs text-text-muted leading-relaxed whitespace-pre-line max-w-[200px] text-center">
              {EMPTY_CHAT_HINT}
            </p>
          </div>
        ) : (
          <>
            {chatMessages.map((msg) => (
              <ChatMessageBubble
                key={msg.id}
                role={msg.role}
                text={msg.text}
                actionLabel={actionLabel(msg.action)}
                actionDisabled={busy}
                onAction={msg.versionId && msg.action === 'retry_render'
                  ? () => void retryFailedRender(msg.versionId as string)
                  : msg.versionId && (msg.action === 'regenerate_video' || msg.action === 'fine_tune_video')
                    ? () => void runChatAction(msg.action as string, msg.versionId as string, msg.id)
                    : undefined}
              />
            ))}
            {busy && <AgentTypingIndicator />}
            <div ref={bottomRef} />
          </>
        )}
        {pendingPatch && (
          <PatchConfirmCard
            patch={pendingPatch}
            onAccept={() => void acceptPatch()}
            onDiscard={() => discardPatch()}
          />
        )}
      </div>

      <div className="px-4 py-3 border-t border-white/8">
        <QuickActionChips onSelect={(text) => void submitMessage(text)} />
      </div>

      <div className="px-4 py-3 border-t border-white/8">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && !busy && void handleSend()}
            placeholder={!projectId ? '正在加载项目 Agent…' : busy ? 'Agent 思考中…' : '输入消息，按 Enter 发送...'}
            disabled={busy || !projectId}
            className="flex-1 px-3 py-2 rounded-lg bg-white/5 border border-white/8 text-sm text-text-main placeholder:text-text-muted focus:outline-none focus:border-primary/40 disabled:opacity-60"
          />
          <button
            type="button"
            onClick={() => void handleSend()}
            disabled={busy || !projectId || !input.trim()}
            className="gradient-btn px-3 py-2 rounded-lg flex items-center gap-1.5 disabled:opacity-60"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
