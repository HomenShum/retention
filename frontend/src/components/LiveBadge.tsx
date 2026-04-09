export function LiveBadge({ isLive }: { isLive: boolean }) {
  return (
    <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
      isLive
        ? 'bg-green-500/10 text-green-400 border border-green-500/20'
        : 'bg-warning/10 text-warning border border-warning/20'
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full ${isLive ? 'bg-green-400' : 'bg-warning'}`} />
      {isLive ? 'Live' : 'Demo'}
    </div>
  )
}
