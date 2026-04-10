import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
} from 'react'
import type { ReactNode } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type MessageRole = 'user' | 'agent' | 'tool_call' | 'system'

export type ToolCallStatus = 'running' | 'success' | 'error'

export interface ToolCallData {
  name: string
  args: Record<string, unknown>
  status: ToolCallStatus
  durationMs?: number
  result?: unknown
  error?: string
}

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  timestamp: number
  toolCall?: ToolCallData
}

interface ChatContextValue {
  messages: ChatMessage[]
  isOpen: boolean
  isConnected: boolean
  togglePanel: () => void
  sendMessage: (text: string) => Promise<void>
  clearChat: () => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let _msgCounter = 0
function nextId(): string {
  _msgCounter += 1
  return `msg_${Date.now()}_${_msgCounter}`
}

function makeMessage(
  role: MessageRole,
  content: string,
  toolCall?: ToolCallData,
): ChatMessage {
  return { id: nextId(), role, content, timestamp: Date.now(), toolCall }
}

// ---------------------------------------------------------------------------
// Local command fallback (when backend is unreachable)
// ---------------------------------------------------------------------------

async function handleLocalCommand(
  text: string,
  pushMessage: (msg: ChatMessage) => void,
): Promise<boolean> {
  const trimmed = text.trim().toLowerCase()

  if (trimmed === 'help') {
    pushMessage(
      makeMessage(
        'agent',
        'Available commands:\n- **scan <url>** -- run a quick QA check\n- **status** -- check backend health\n- **help** -- show this message',
      ),
    )
    return true
  }

  if (trimmed === 'status') {
    pushMessage(
      makeMessage('tool_call', 'Checking health...', {
        name: 'health_check',
        args: {},
        status: 'running',
      }),
    )
    try {
      const res = await fetch('/health')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = (await res.json()) as Record<string, unknown>
      pushMessage(
        makeMessage('tool_call', JSON.stringify(body, null, 2), {
          name: 'health_check',
          args: {},
          status: 'success',
          result: body,
        }),
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      pushMessage(
        makeMessage('tool_call', message, {
          name: 'health_check',
          args: {},
          status: 'error',
          error: message,
        }),
      )
    }
    return true
  }

  const scanMatch = trimmed.match(/^scan\s+(.+)$/)
  if (scanMatch) {
    const url = scanMatch[1]
    pushMessage(
      makeMessage('tool_call', `Scanning ${url}...`, {
        name: 'retention.qa_check',
        args: { url },
        status: 'running',
      }),
    )
    try {
      const res = await fetch('/api/qa/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = (await res.json()) as Record<string, unknown>
      pushMessage(
        makeMessage('tool_call', JSON.stringify(body, null, 2), {
          name: 'retention.qa_check',
          args: { url },
          status: 'success',
          result: body,
        }),
      )
      pushMessage(makeMessage('agent', `Scan complete for ${url}.`))
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      pushMessage(
        makeMessage('tool_call', message, {
          name: 'retention.qa_check',
          args: { url },
          status: 'error',
          error: message,
        }),
      )
      pushMessage(
        makeMessage('agent', `Scan failed: ${message}. Is the backend running?`),
      )
    }
    return true
  }

  return false
}

// ---------------------------------------------------------------------------
// SSE stream parser
// ---------------------------------------------------------------------------

async function streamChat(
  text: string,
  history: ChatMessage[],
  pushMessage: (msg: ChatMessage) => void,
  updateMessage: (id: string, patch: Partial<ChatMessage>) => void,
  signal: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: text, history }),
    signal,
  })

  if (!res.ok) {
    throw new Error(`Backend returned ${res.status}`)
  }

  const contentType = res.headers.get('content-type') ?? ''

  // Non-streaming JSON fallback
  if (!contentType.includes('text/event-stream')) {
    const body = (await res.json()) as { content?: string }
    pushMessage(makeMessage('agent', body.content ?? JSON.stringify(body)))
    return
  }

  // SSE streaming
  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error('No readable stream')
  }
  const decoder = new TextDecoder()
  let buffer = ''

  // Track current agent message for incremental appends
  let currentAgentId: string | null = null

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    // Keep the last potentially-incomplete line in buffer
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const raw = line.slice(6).trim()
      if (raw === '[DONE]') return

      let event: {
        type?: string
        content?: string
        tool_name?: string
        args?: Record<string, unknown>
        status?: ToolCallStatus
        duration_ms?: number
        result?: unknown
        error?: string
        id?: string
      }
      try {
        event = JSON.parse(raw) as typeof event
      } catch {
        continue
      }

      if (event.type === 'agent' || event.type === 'text') {
        const chunk = event.content ?? ''
        if (currentAgentId) {
          // Append to existing agent message
          updateMessage(currentAgentId, {
            content: chunk,
          })
        } else {
          const msg = makeMessage('agent', chunk)
          currentAgentId = msg.id
          pushMessage(msg)
        }
      } else if (event.type === 'tool_call') {
        // Tool calls break the current agent message stream
        currentAgentId = null
        const toolCall: ToolCallData = {
          name: event.tool_name ?? 'unknown',
          args: event.args ?? {},
          status: event.status ?? 'running',
          durationMs: event.duration_ms,
          result: event.result,
          error: event.error,
        }
        const id = event.id ?? nextId()
        pushMessage({
          id,
          role: 'tool_call',
          content: event.content ?? '',
          timestamp: Date.now(),
          toolCall,
        })
      } else if (event.type === 'tool_result') {
        // Update an existing tool_call message
        currentAgentId = null
        if (event.id) {
          updateMessage(event.id, {
            toolCall: {
              name: event.tool_name ?? 'unknown',
              args: event.args ?? {},
              status: event.status ?? 'success',
              durationMs: event.duration_ms,
              result: event.result,
              error: event.error,
            },
          })
        }
      } else if (event.type === 'system') {
        currentAgentId = null
        pushMessage(makeMessage('system', event.content ?? ''))
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Context + Provider
// ---------------------------------------------------------------------------

const ChatContext = createContext<ChatContextValue | null>(null)

export function ChatProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isOpen, setIsOpen] = useState(false)
  const [isConnected, setIsConnected] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // Probe backend connectivity once on mount, then every 30s
  useEffect(() => {
    let cancelled = false
    async function probe() {
      try {
        const res = await fetch('/health', { method: 'GET' })
        if (!cancelled) setIsConnected(res.ok)
      } catch {
        if (!cancelled) setIsConnected(false)
      }
    }
    probe()
    const interval = setInterval(() => { void probe() }, 30_000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [])

  const pushMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg])
  }, [])

  const updateMessage = useCallback(
    (id: string, patch: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== id) return m
          // For agent text streaming: append content
          if (patch.content !== undefined && m.role === 'agent') {
            return { ...m, content: m.content + patch.content }
          }
          return { ...m, ...patch }
        }),
      )
    },
    [],
  )

  const togglePanel = useCallback(() => setIsOpen((o) => !o), [])

  const clearChat = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
  }, [])

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed) return

      // Push user message
      const userMsg = makeMessage('user', trimmed)
      pushMessage(userMsg)

      // If not connected, try local command parser
      if (!isConnected) {
        const handled = await handleLocalCommand(trimmed, pushMessage)
        if (!handled) {
          pushMessage(
            makeMessage(
              'system',
              'Backend unreachable. Try "help" for available offline commands.',
            ),
          )
        }
        return
      }

      // Stream from backend
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      try {
        await streamChat(
          trimmed,
          messages,
          pushMessage,
          updateMessage,
          controller.signal,
        )
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        // Fallback to local commands on network failure
        setIsConnected(false)
        const handled = await handleLocalCommand(trimmed, pushMessage)
        if (!handled) {
          pushMessage(
            makeMessage(
              'system',
              `Connection lost: ${(err as Error).message}. Falling back to offline mode.`,
            ),
          )
        }
      }
    },
    [isConnected, messages, pushMessage, updateMessage],
  )

  return (
    <ChatContext.Provider
      value={{ messages, isOpen, isConnected, togglePanel, sendMessage, clearChat }}
    >
      {children}
    </ChatContext.Provider>
  )
}

export function useChat(): ChatContextValue {
  const ctx = useContext(ChatContext)
  if (!ctx) throw new Error('useChat must be used within <ChatProvider>')
  return ctx
}
