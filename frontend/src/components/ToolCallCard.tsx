import { useState } from 'react'
import { CheckCircle, XCircle, ChevronDown, ChevronRight, Loader2 } from 'lucide-react'
import type { ToolCallData } from '../contexts/ChatContext'

interface ToolCallCardProps {
  toolCall: ToolCallData
}

function truncateArgs(args: Record<string, unknown>, maxLen = 80): string {
  const pairs = Object.entries(args).map(
    ([k, v]) => `${k}: ${typeof v === 'string' ? `"${v}"` : JSON.stringify(v)}`,
  )
  const full = pairs.join(', ')
  return full.length > maxLen ? full.slice(0, maxLen) + '...' : full
}

export function ToolCallCard({ toolCall }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false)

  const { name, args, status, durationMs, result, error } = toolCall

  const borderColor =
    status === 'running'
      ? 'border-accent'
      : status === 'success'
        ? 'border-accent'
        : 'border-danger'

  const StatusIcon =
    status === 'running'
      ? Loader2
      : status === 'success'
        ? CheckCircle
        : XCircle

  const statusColor =
    status === 'running'
      ? 'text-accent'
      : status === 'success'
        ? 'text-accent'
        : 'text-danger'

  return (
    <div
      className={`border-l-2 ${borderColor} bg-bg-card rounded-r-lg px-3 py-2 my-1 ${
        status === 'running' ? 'animate-pulse' : ''
      }`}
    >
      {/* Header row */}
      <div className="flex items-center gap-2 min-w-0">
        <StatusIcon
          className={`w-3.5 h-3.5 shrink-0 ${statusColor} ${
            status === 'running' ? 'animate-spin' : ''
          }`}
        />
        <span className="font-mono text-xs text-text-primary truncate">
          {name}
        </span>
        {durationMs !== undefined && (
          <span className="text-[10px] text-text-muted ml-auto shrink-0">
            {durationMs}ms
          </span>
        )}
      </div>

      {/* Args summary */}
      {Object.keys(args).length > 0 && (
        <div className="font-mono text-[11px] text-text-muted mt-1 truncate">
          {truncateArgs(args)}
        </div>
      )}

      {/* Error message */}
      {status === 'error' && error && (
        <div className="text-xs text-danger mt-1">{error}</div>
      )}

      {/* Expandable result */}
      {status !== 'running' && result !== undefined && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 mt-1.5 text-[11px] text-text-muted hover:text-text-secondary transition-colors cursor-pointer bg-transparent border-none p-0"
        >
          {expanded ? (
            <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronRight className="w-3 h-3" />
          )}
          {expanded ? 'Hide result' : 'Show result'}
        </button>
      )}

      {expanded && result !== undefined && (
        <pre className="mt-1.5 p-2 bg-bg-primary rounded text-[11px] font-mono text-text-secondary overflow-x-auto max-h-48 overflow-y-auto">
          {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
        </pre>
      )}
    </div>
  )
}
