import React from 'react';

interface ChatMessageBubbleProps {
  role: 'user' | 'agent';
  text: string;
  actionLabel?: string;
  onAction?: () => void;
  actionDisabled?: boolean;
}

export default function ChatMessageBubble({ role, text, actionLabel, onAction, actionDisabled }: ChatMessageBubbleProps) {
  const isUser = role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} animate-fade-in-up`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0 mr-2 mt-0.5">
          <div className="w-2 h-2 rounded-full bg-primary-light" />
        </div>
      )}
      <div
        className={`max-w-[85%] px-4 py-2.5 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap break-words ${
          isUser
            ? 'bg-primary/20 text-text-main rounded-tr-sm border border-primary/20'
            : 'glass rounded-tl-sm text-text-secondary'
        }`}
      >
        <div>{text}</div>
        {actionLabel && onAction && (
          <button
            type="button"
            onClick={onAction}
            disabled={actionDisabled}
            className="mt-3 w-full rounded-xl border border-primary/35 bg-primary/15 px-3 py-2 text-sm font-semibold text-primary-light transition-colors hover:bg-primary/25 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {actionLabel}
          </button>
        )}
      </div>
      {isUser && (
        <div className="w-7 h-7 rounded-full bg-secondary/20 flex items-center justify-center flex-shrink-0 ml-2 mt-0.5">
          <div className="w-2 h-2 rounded-full bg-secondary" />
        </div>
      )}
    </div>
  );
}
