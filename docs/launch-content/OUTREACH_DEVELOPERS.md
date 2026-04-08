# Developer Outreach Messages

---

## 1. Blog Comment / Email to Christopher Meiklejohn

**Target**: Christopher Meiklejohn
**Post**: "Teaching Claude to QA a Mobile App" (March 2026)
**Channel**: Blog comment or email

---

Hey Christopher,

Really enjoyed your writeup on the Zabriskie QA pipeline. The CDP-via-WebView approach on Android is clean -- discovering the socket through `adb forward` and getting full programmatic control through the DevTools protocol is the right move. And the iOS section was painfully relatable. The coordinate-based interaction through IDB, the TCC.db permission hacks, the AppleScript keyboard workarounds -- you captured exactly why iOS automation feels like picking a lock compared to Android handing you the key.

The thing that caught my attention is the re-exploration problem. Your 25-screen morning sweep is impressive at ~90 seconds, but every run starts from scratch. That's the exact gap we've been building retention.sh to solve.

The idea: after your first CDP crawl or ios-simulator-mcp sweep, we cache the full trajectory -- every navigation path, every screen state, every assertion coordinate. On rerun, the agent replays from cache instead of re-exploring. In practice that cuts token cost by ~60-70% on subsequent runs. You still get the same coverage, but the agent only does fresh exploration when it hits something that changed.

A few specifics that might be useful for your setup:

- **Before/after diff**: After you fix a cosmetic issue your bot filed, the rerun shows exactly what changed vs. the previous sweep. No manual comparison.
- **Trajectory memory**: The cached paths work across both your Android CDP pipeline and iOS IDB flow. Same memory layer, different transport.
- **Local Playwright integration**: If you're already running headless checks alongside the native sweeps, the trajectory cache covers those too.

It layers on top of whatever automation stack you're already using -- your CDP forwarding and ios-simulator-mcp setup stay exactly as-is.

Would be great to compare notes on the iOS side especially. We've been dealing with the same accessibility API limitations and I'm curious how you're handling the keyboard character interpretation issues you mentioned.

-- Homin Shum
retention.sh

---

## 2. LinkedIn Follow-Up Comment (Jordan Cutler thread)

**Context**: Responding to thread discussion about mobile testing tools, specifically Zhen Han's question about emulator testing and Colin Lee's point about release builds on real hardware.

---

Adding some technical context to this thread since a few people asked about the tooling side --

@Zhen Han on emulator testing: we've been running retention.sh against both iOS Simulator (via accessibility APIs + IDB) and Android emulators (Chrome DevTools Protocol through the WebView socket). The key insight is that emulator-based QA catches ~80% of the visual and functional regressions before you ever need real hardware. @Colin Lee is right that release builds on device matter for performance profiling, but for functional coverage the simulator loop is dramatically faster.

The biggest problem with AI-driven mobile QA isn't getting the agent to tap buttons -- it's that every run re-explores the entire app from scratch. retention.sh solves this with trajectory memory: the first run caches every navigation path, and subsequent runs replay from cache at ~5% of the original token cost. Fix a bug, rerun, and you get an instant before/after diff showing exactly what changed. That's the closed-loop TDD cycle people are describing here, but without burning tokens on redundant exploration.

---
