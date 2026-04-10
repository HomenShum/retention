/**
 * useChat — re-exports the context hook for convenience.
 *
 * All heavy lifting (SSE streaming, local fallback, message state) lives in
 * ChatContext.  This file exists so consumers can import from `hooks/useChat`
 * which matches the project convention of hooks/ for all custom hooks.
 */

export { useChat } from '../contexts/ChatContext'
export type {
  ChatMessage,
  MessageRole,
  ToolCallData,
  ToolCallStatus,
} from '../contexts/ChatContext'
