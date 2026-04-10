import type { ChatMessage as ChatMessageType } from '../contexts/ChatContext'
import { ToolCallCard } from './ToolCallCard'

interface ChatMessageProps {
  message: ChatMessageType
}

export function ChatMessage({ message }: ChatMessageProps) {
  const { role, content, toolCall } = message

  // ---- tool_call: delegate to ToolCallCard ----
  if (role === 'tool_call' && toolCall) {
    return (
      <div className="px-3 py-1">
        <ToolCallCard toolCall={toolCall} />
      </div>
    )
  }

  // ---- system: centered muted ----
  if (role === 'system') {
    return (
      <div className="flex justify-center px-3 py-1.5">
        <span className="text-[11px] text-text-muted text-center max-w-[80%]">
          {content}
        </span>
      </div>
    )
  }

  // ---- user: right-aligned ----
  if (role === 'user') {
    return (
      <div className="flex justify-end px-3 py-1">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent/10 text-text-primary text-[13px] leading-relaxed px-3.5 py-2">
          {content}
        </div>
      </div>
    )
  }

  // ---- agent: left-aligned ----
  return (
    <div className="flex justify-start px-3 py-1">
      <div className="max-w-[85%] rounded-2xl rounded-bl-md bg-bg-card text-text-secondary text-[13px] leading-relaxed px-3.5 py-2 whitespace-pre-wrap">
        {content}
      </div>
    </div>
  )
}
