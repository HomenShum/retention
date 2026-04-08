# retention.sh — Onboarding Guide

## Don't Want to Read? Paste This Into Claude Code

```
I need to set up retention.sh locally. Here's what to do:

1. Clone and install:
   cd backend && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

2. Start the backend:
   python -m uvicorn app.main:app --reload --port 8000

3. In a second terminal, start the frontend:
   cd frontend/test-studio && npm install && npm run dev

4. Open http://localhost:5173/signin and sign in with Google or GitHub.

5. To see the Run Inspector with real data: http://localhost:5173/run-inspector

6. To join a team (if you have an invite code):
   http://localhost:5173/signin?team=PASTE_CODE_HERE&redirect=/dashboard

That's it. Backend on 8000, frontend on 5173, sign in, inspect runs.
```

---

## The 30-Second Version

retention.sh watches what AI coding agents do and checks if they actually finished the job.

Think of it like a driving instructor sitting next to a student driver. The student (AI agent) does the driving. The instructor (retention.sh) watches every turn, checks mirrors, confirms the parking brake. If the student says "I'm done" but forgot to check the blind spot, the instructor says "not yet — you missed this."

That's what TA does for code. The AI writes code. TA checks if it really works. If steps were skipped, TA names them. If the run was good, TA proves it.

---

## What You'll See When You Open It

| Page | URL | What it shows |
|------|-----|--------------|
| **Sign In** | `/signin` | Google, GitHub, email, or magic link auth |
| **Run Inspector** | `/run-inspector` | Every tool call the agent made, which workflow steps were covered, what's missing, the verdict |
| **Dashboard** | `/dashboard` | Team stats, shared trajectories, members, savings |
| **Benchmarks** | `/benchmarks` | How well the system performs across different tasks |

---

## How Teams Work

1. One person creates a team (gets a 6-letter invite code like `K7XM2P`)
2. Share this link with teammates: `https://test-studio-xi.vercel.app/signin?team=K7XM2P`
3. They sign in, auto-join the team, see shared data
4. For CLI users: `RETENTION_TEAM=K7XM2P curl -sL retention.sh/install.sh | bash`

No admin panels. No role management. Everyone on the team sees everything. Simple.

---

This document is for everyone: new engineers, founders, advisors, and investors. No prior knowledge of the codebase is assumed. Read the section that applies to you.

---

## What is retention.sh?

retention.sh is a **QA automation platform** that sits inside the AI coding agent workflow.

Here is the problem it solves: AI coding tools like Claude Code can write code, but they cannot actually verify that the code works in a running app. When they try, they take guesses from screenshots, retry blindly 2–5 times, and still can't tell you which exact step broke or which file to fix. Developers end up doing that verification manually — which defeats the point of having an AI agent.

retention.sh closes that loop. It runs real test flows on your app (in a browser or on an Android emulator), captures structured evidence, and feeds a compact, actionable failure report back to the AI agent — so the agent can fix precisely and verify the fix worked.

The net result: **97% fewer wasted AI tokens**, faster fixes, and proof that the app actually works.

---

## How It Works — The Core Loop

```
1. Developer asks Claude Code to fix a bug
2. Claude Code patches the code, then calls retention.sh via MCP
3. retention.sh runs the real app flow (browser or Android emulator)
4. Evidence is captured: screenshots, traces, console logs, video
5. A compact failure bundle is returned: exact failing step, root cause, files to fix
6. Claude Code reads the bundle, patches precisely
7. retention.sh reruns and compares before/after
8. Verdict: PASS ✓ — the loop is closed
```

---

## The Three Parts of the System

retention.sh has three main pieces working together:

| Part | What it is | What it does |
|------|-----------|--------------|
| **Backend** (`backend/`) | Python + FastAPI | Hosts the AI agents, drives emulators, runs QA flows, exposes the API |
| **Frontend** (`frontend/test-studio/`) | React + TypeScript + Vite | The dashboard where you see test results, inspect runs, manage your team |
| **Database** (`convex/`) | Convex (real-time DB) | Stores users, teams, test trajectories, tokens, and shared results |

---

## Key Features

### 🤖 AI Agents
A hierarchical multi-agent system (Coordinator → Specialists) powers all test execution:
- **Coordinator Agent**: Receives the task, routes to the right specialist
- **Device Testing Agent**: Drives the Android emulator using Mobile MCP + ADB fallback
- **Test Generation Agent**: Converts app crawl data into structured test cases
- **Search Agent**: Looks up past runs, known bugs, and relevant context

The agents use the **OAVR loop** (Observe → Act → Verify → Reason) to navigate apps autonomously. If a step fails, a Failure Diagnosis sub-agent suggests recovery strategies.

### 📋 Golden Bugs
10 deterministic, reproducible test cases based on real Instagram workflows. These are used as a fixed benchmark — every significant code change is measured against them to produce a precision/recall/F1 score. This prevents regressions and gives an honest picture of agent quality.

### 🎬 ActionSpan Clips
Instead of reviewing an entire test session (expensive, slow), retention.sh captures 2–3 second video clips around each verification point. These clips are **~7x cheaper** to review than full session recordings and give precise visual proof of exactly what happened at each step.

### 🧠 Trajectory Memory & Session Replay
Every test run is stored as a "trajectory" — a full record of every action, screenshot, and verification in sequence. Trajectories can be:
- **Replayed** to re-run the exact same workflow without re-crawling the app
- **Compared** to detect regressions between two runs (before/after a fix)
- **Compressed** to remove redundant steps and reduce future run costs
- **Packaged** as TCWP (Canonical Workflow Packages) for sharing with partners

### 🔍 Run Inspector (`/run-inspector`)
The Run Inspector is the primary tool for reviewing a completed test run. It shows:
- The full sequence of agent actions with screenshots
- The structured verdict (PASS / FAIL / BLOCKED) with a confidence score
- Root cause candidates with suggested files to fix
- The "Share with Team" button to send results to a colleague

### 📊 Compliance Dashboard & Benchmarks
The `/dashboard` and `/benchmarks` pages show team-level metrics: total runs, tokens saved, pass rates, and comparative benchmarks across multiple model providers (GPT-5.4, Claude, Gemini).

### 🎨 Figma → QA Pipeline
Point retention.sh at a Figma design file and it will:
1. Analyze the frames and extract visual user flows
2. Generate code (React or HTML) from the design
3. Deploy it and run a full QA pipeline against it — all in one command

### 📡 MCP Integration
retention.sh exposes itself as an MCP (Model Context Protocol) server, which means any AI coding agent (Claude Code, Cursor, Windsurf) can call it directly as a tool — no glue code needed. Key tools available via MCP:

| Tool | What it does |
|------|-------------|
| `ta.quickstart` | One-call QA: checks environment, runs full pipeline |
| `ta.run_web_flow` | Full browser QA on any URL |
| `ta.run_android_flow` | Full QA on a native Android app |
| `ta.pipeline.failure_bundle` | Get a compact bug report (<200 tokens) |
| `ta.pipeline.rerun_failures` | Re-run only failed tests after a fix (~10 seconds) |
| `ta.compare_before_after` | Diff two runs to detect regressions |
| `ta.emit_verdict` | Record a structured pass/fail verdict |

---

## Authentication & Team Sharing

We recently added a secure sign-in and team sharing system so advisors can collaborate with engineers on test results.

**Sign In (`/signin`):** Supports Google, GitHub, Magic Link, and Email/Password — powered by Convex Auth.

**Protected Pages:** `/run-inspector` and `/dashboard` require sign-in. If you're not signed in, you're redirected to `/signin` automatically.

**Sharing a test run with a teammate:**
1. Advisor opens a test run in the Run Inspector
2. Clicks **"Share with Team"** → a link is generated: `.../signin?team=K7XM2P&redirect=/run-inspector`
3. Advisor sends the link to the engineer
4. Engineer clicks the link, signs up or signs in, and is **automatically added to the team** — no manual invite needed
5. Engineer immediately sees the shared run data

---

## Quick Start — Get It Running Locally

Paste these commands into your terminal (or into Claude Code):

```bash
# Terminal 1 — Start the Backend (AI agents + API)
cd backend
python -m venv .venv
source .venv/bin/activate        # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

```bash
# Terminal 2 — Start the Frontend + Database
cd frontend/test-studio
npm install
npx convex dev &                 # Starts the real-time database
npm run dev                      # Starts the React app on port 5173
```

Then open **http://localhost:5173/signin** in your browser.

---

## For Investors & Non-Technical Stakeholders

**The market problem:** Software teams using AI coding agents still manually verify that the code works. This is the last un-automated step in the development loop — and it's expensive and slow.

**What retention.sh does:** Automates that verification step. The AI agent writes code; retention.sh proves it works; the loop closes without a human in the middle.

**Why it's defensible:**
- Every test run builds a library of replayable trajectories (memory that compounds over time)
- Trajectory replay makes each subsequent run faster and cheaper (97% token savings measured)
- The judgment layer (structured failure bundles + LLM-as-judge) is not replicable by simply wiring Playwright to an MCP — we provide evidence structure, not just execution
- Local-first architecture means zero hosting costs for compute; customers pay for intelligence, not infrastructure

**Current state (honest):**
- Web QA, Android emulator QA, evidence capture, failure bundles, verdict scoring, before/after comparison — all working
- MCP integration with Claude Code — verified
- Benchmarks against 10 Golden Bug test cases — operational
- Trajectory memory, compression, and TCWP packaging — operational
- Physical device testing and enterprise SSO — not yet built

---

## Key Files for New Engineers

| File | What to read it for |
|------|-------------------|
| `AGENTS.md` | Deep technical reference for agent architecture, patterns, and known gotchas |
| `CLAUDE.md` | Quick commands, conventions, and testing instructions |
| `docs/one-pager-what-we-are.md` | The product positioning and wedge explained concisely |
| `backend/app/main.py` | All API routes registered in one place |
| `frontend/test-studio/src/App.tsx` | All frontend routes in one place |
| `frontend/test-studio/convex/schema.ts` | The full database schema (users, teams, trajectories, tokens) |
| `backend/data/golden_bugs.json` | The 10 canonical test cases used for benchmarking |
