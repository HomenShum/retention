import { useRef, useEffect, useState, useCallback } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import { X, Mic, MicOff, Send, MessageSquare, Trash2 } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { useVoice } from '../hooks/useVoice'
import { ChatMessage } from './ChatMessage'

// ---------------------------------------------------------------------------
// Suggestion chips shown when the chat is empty
// ---------------------------------------------------------------------------

const SUGGESTIONS = [
  'Scan a URL',
  'Show last run',
  'What can you do?',
] as const

// ---------------------------------------------------------------------------
// ChatPanel
// ---------------------------------------------------------------------------

export function ChatPanel() {
  const { messages, isOpen, isConnected, togglePanel, sendMessage, clearChat } =
    useChat()

  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)

  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Voice integration: on final result, send as message
  const handleVoiceFinal = useCallback(
    (text: string) => {
      void sendMessage(text)
    },
    [sendMessage],
  )
  const { isListening, isSupported, startListening, stopListening, transcript } =
    useVoice(handleVoiceFinal)

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    const el = scrollRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  }, [messages])

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) {
      // Small delay lets the CSS transition start before focusing
      const timer = setTimeout(() => inputRef.current?.focus(), 150)
      return () => clearTimeout(timer)
    }
  }, [isOpen])

  // Keyboard: Escape closes panel
  useEffect(() => {
    function handleKey(e: globalThis.KeyboardEvent) {
      if (e.key === 'Escape' && isOpen) {
        togglePanel()
      }
    }
    document.addEventListener('keydown', handleKey)
    return () => document.removeEventListener('keydown', handleKey)
  }, [isOpen, togglePanel])

  // Submit handler
  const handleSubmit = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault()
      const text = input.trim()
      if (!text || sending) return
      setInput('')
      setSending(true)
      try {
        await sendMessage(text)
      } finally {
        setSending(false)
      }
    },
    [input, sending, sendMessage],
  )

  // Enter sends, Shift+Enter is newline (though we use input not textarea)
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        void handleSubmit()
      }
    },
    [handleSubmit],
  )

  // Suggestion chip click
  const handleSuggestion = useCallback(
    (text: string) => {
      setInput('')
      void sendMessage(text)
    },
    [sendMessage],
  )

  return (
    <>
      {/* Backdrop overlay on mobile */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-40 md:hidden"
          onClick={togglePanel}
        />
      )}

      {/* Panel */}
      <div
        className={`fixed top-0 right-0 h-full z-50 flex flex-col
          w-full md:w-[400px]
          bg-bg-surface border-l border-border-subtle
          transition-transform duration-300 ease-in-out
          ${isOpen ? 'translate-x-0' : 'translate-x-full'}
        `}
      >
        {/* ---- Header ---- */}
        <header className="flex items-center gap-2 px-4 py-3 border-b border-border-subtle shrink-0">
          <MessageSquare className="w-4 h-4 text-accent" />
          <span className="text-sm font-semibold text-text-primary flex-1">
            retention.sh
          </span>

          {/* Connection badge */}
          <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium ${
              isConnected
                ? 'bg-green-500/10 text-green-400'
                : 'bg-warning/10 text-warning'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                isConnected ? 'bg-green-400' : 'bg-warning'
              }`}
            />
            {isConnected ? 'Live' : 'Offline'}
          </span>

          {/* Mic button */}
          {isSupported && (
            <button
              onClick={isListening ? stopListening : startListening}
              className={`p-1.5 rounded-lg transition-colors cursor-pointer border-none ${
                isListening
                  ? 'bg-accent/20 text-accent'
                  : 'text-text-muted hover:text-text-secondary hover:bg-white/[0.04]'
              }`}
              title={isListening ? 'Stop listening' : 'Voice input'}
            >
              {isListening ? (
                <MicOff className="w-4 h-4" />
              ) : (
                <Mic className="w-4 h-4" />
              )}
            </button>
          )}

          {/* Clear chat */}
          {messages.length > 0 && (
            <button
              onClick={clearChat}
              className="p-1.5 rounded-lg text-text-muted hover:text-text-secondary hover:bg-white/[0.04] transition-colors cursor-pointer border-none"
              title="Clear chat"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}

          {/* Close */}
          <button
            onClick={togglePanel}
            className="p-1.5 rounded-lg text-text-muted hover:text-text-secondary hover:bg-white/[0.04] transition-colors cursor-pointer border-none"
            title="Close (Esc)"
          >
            <X className="w-4 h-4" />
          </button>
        </header>

        {/* ---- Messages ---- */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto py-3 space-y-1">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-4 px-6">
              <MessageSquare className="w-10 h-10 text-text-muted opacity-30" />
              <p className="text-sm text-text-muted text-center">
                Talk to retention.sh like a QA teammate.
              </p>

              {/* Suggestion chips */}
              <div className="flex flex-wrap gap-2 justify-center">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => handleSuggestion(s)}
                    className="px-3 py-1.5 rounded-full text-xs text-text-secondary border border-border-subtle hover:border-accent/40 hover:text-accent transition-colors cursor-pointer bg-transparent"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((msg) => <ChatMessage key={msg.id} message={msg} />)
          )}
        </div>

        {/* ---- Voice transcript preview ---- */}
        {isListening && transcript && (
          <div className="px-4 py-1.5 text-xs text-accent/80 italic border-t border-border-subtle">
            {transcript}
          </div>
        )}

        {/* ---- Input bar ---- */}
        <form
          onSubmit={(e) => { void handleSubmit(e) }}
          className="flex items-center gap-2 px-3 py-3 border-t border-border-subtle shrink-0"
        >
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isListening ? 'Listening...' : 'Ask retention.sh...'}
            disabled={sending}
            className="flex-1 bg-bg-card border border-border-subtle rounded-lg px-3 py-2 text-sm text-text-primary placeholder-text-muted outline-none focus:border-accent/50 transition-colors disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!input.trim() || sending}
            className="p-2 rounded-lg bg-accent text-black disabled:opacity-30 hover:bg-accent-muted transition-colors cursor-pointer border-none shrink-0"
            title="Send (Enter)"
          >
            <Send className="w-4 h-4" />
          </button>
        </form>
      </div>
    </>
  )
}
