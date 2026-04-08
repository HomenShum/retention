---
description: Append a short spoken-style recap alongside the final response
---

## Overview

Use this when the user asks for a "voice memo", "audio recap", or a spoken-style summary but an actual audio file is not being generated.

The goal is to sound like a 20–30 second voicemail, not a written report.

## Output format

1. Give the normal written answer first.
2. Add a final block labeled `Voice memo`.
3. Keep the voice memo to 3 short sentences:
   - What happened
   - Why it matters
   - What happens next

## Style rules

- Plain English only
- No file paths unless the user explicitly wants technical detail
- No bullet overload inside the voice memo
- Readable out loud in one breath
- Prefer one concrete status word: `live`, `fixed`, `blocked`, `next`

## Template

```text
Voice memo: Quick update — [what happened]. This matters because [why it matters]. Next I’d [next step].
```

## Example

```text
Voice memo: Quick update — the installer is live and serving the right shell script again. This matters because people can use the one-line setup without landing on broken app HTML. Next I’d wire an end-to-end smoke test that also checks token generation, not just the shell script contract.
```
