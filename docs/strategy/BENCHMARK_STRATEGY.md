# retention.sh: Benchmark Strategy

*How we prove value against recognizable baselines, starting with BrowserStack.*

---

## 1. Why Benchmark

Benchmarks are the fastest way to:
- Build credibility with buyers
- Create shareable proof artifacts
- Establish a clear "why TA" message
- Generate inbound interest
- Anchor sales conversations

We need benchmarks that outsiders can understand immediately.

---

## 2. BrowserStack as First Baseline

### Why BrowserStack

- Universally recognized brand in testing/device clouds
- Familiar market baseline for engineering leaders
- Clear cost structure (per-session, per-minute pricing)
- Makes our value proposition concrete: "Here's what a full session costs there. Here's what a trajectory replay costs with TA."

### What We Are NOT Saying

- We are NOT saying "TA replaces BrowserStack"
- We ARE saying "TA adds a workflow intelligence layer that makes any execution platform cheaper over time"
- BrowserStack provides execution breadth (devices, browsers, OS versions)
- TA provides execution depth (memory, replay, compression, audit)

### Benchmark Structure

| Dimension | BrowserStack Baseline | retention.sh Replay | Delta |
|-----------|----------------------|-----------------|-------|
| Session cost | $X per session | $Y per replay | % savings |
| Setup time | Manual / scripted | Trajectory-guided | % faster |
| Rerun cost | Full session again | Partial replay | % savings |
| Failure diagnosis | Manual review | Structured failure bundle | Qualitative |
| Verification evidence | Screenshot + logs | TCWP package (events, checkpoints, states, evals) | Richer |
| Memory across runs | None (stateless) | Trajectory + compression history | Compounding |

---

## 3. Benchmark Methodology

### Principles

1. **Reproducible** — Anyone can run the same benchmark
2. **Fair** — Compare equivalent workflows, not cherry-picked scenarios
3. **Transparent** — Publish methodology, not just results
4. **Durable** — Results should be stable across N=1/5/10 runs

### Benchmark Workflow Selection

Choose 3-5 workflows that are:
- Commonly needed (profile edit, login, checkout, search, form fill)
- Cross-app applicable (not tied to one specific app)
- Measurable (clear success criteria)
- Varying complexity (simple → medium → complex)

### Proposed Benchmark Suite

| # | Workflow | Complexity | Surface | Steps (est.) |
|---|---------|-----------|---------|-------------|
| 1 | Profile Edit | Simple | Mobile (Android) | 10-15 |
| 2 | Login + Verify | Simple | Browser | 5-8 |
| 3 | Search + Filter + Select | Medium | Mobile/Browser | 15-20 |
| 4 | Form Fill + Submit | Medium | Browser | 12-18 |
| 5 | Multi-Step Checkout | Complex | Browser | 20-30 |

### Measurement Protocol

For each workflow:

1. **Baseline (full crawl):** Run workflow from scratch 5 times
   - Record: tokens, time, cost, steps, checkpoints, pass rate
   - Calculate: mean, median, p95 for each metric

2. **Trajectory capture:** Extract best trajectory from baseline runs
   - Record: trajectory steps, compression ratio

3. **Replay (N=5):** Run trajectory replay 5 times
   - Record: same metrics as baseline
   - Calculate: savings vs baseline mean

4. **Durability (N=10):** Run trajectory replay 10 times over 3 days
   - Record: drift incidents, checkpoint failures
   - Calculate: durability score (successful replays / total)

5. **Compression (if applicable):** Run optimization pipeline
   - Record: steps eliminated, additional savings
   - Audit: verify compressed trajectory produces same end state

### Reporting Format

Each benchmark produces a TCWP sales_brief with:
- Savings table (tokens, time, cost, steps)
- Durability metrics (N=1/5/10 pass rates)
- Compression history (if applicable)
- Comparison against baseline (BrowserStack session cost equivalent)

---

## 4. BrowserStack Cost Comparison

### BrowserStack Pricing Reference

| Plan | Cost | Per-Session Estimate |
|------|------|---------------------|
| Free | $0 | N/A (limited) |
| Desktop Browser | $29-$249/mo | $0.10-$0.50/session |
| Real Device | $25-$399/mo | $0.25-$1.00/session |
| Automate | $99-$899/mo | $0.15-$0.75/session |
| Enterprise | Custom | Custom |

### retention.sh Cost Model

| Run Type | Est. Token Cost | Est. Total Cost |
|----------|----------------|-----------------|
| Full crawl (baseline) | $0.05-$0.10 | $0.05-$0.10 |
| Trajectory replay | $0.01-$0.03 | $0.01-$0.03 |
| Compressed replay | $0.005-$0.015 | $0.005-$0.015 |
| Audit run | $0.01-$0.02 | $0.01-$0.02 |

### Key Delta

TA replay gets cheaper over time. BrowserStack sessions cost the same every time.

```
BrowserStack: $0.25/session × 100 runs = $25.00
retention.sh: $0.10 (baseline) + $0.013 × 99 (replays) = $1.39
Savings: 94.4%
```

---

## 5. Beyond BrowserStack

### Future Benchmark Targets

| Baseline | Why | When |
|----------|-----|------|
| BrowserStack | Recognizable, clear pricing | Now (Phase 1) |
| Manual QA | Hours vs minutes comparison | Phase 2 |
| Momentic / AI testing tools | AI-native comparison | Phase 2 |
| Raw agent runs (no TA) | Pure agent overhead | Phase 1 |

### Benchmark as Sales Tool

Every benchmark page should include:
- Clear methodology
- Reproducible steps
- Real numbers (not projected)
- TCWP bundle download
- "Try it yourself" CTA
- Contact for pilot

---

## 6. Publication Plan

### Week 3-4: First Benchmark
- Profile Edit Flow (mobile, Android)
- Full crawl vs trajectory replay
- N=5 durability
- Publish as hosted page on Vercel

### Week 5-6: BrowserStack Comparison
- Same workflow, BrowserStack session cost vs TA replay cost
- Side-by-side savings table
- Publish as shareable benchmark page

### Week 7-8: Multi-Workflow Suite
- 3-5 workflows across mobile + browser
- Aggregate savings
- Compression results
- Publish as case study

---

## 7. Benchmark Infrastructure

### Required Components
- Android emulator (local or CI)
- Browser automation (Playwright)
- TCWP package generator
- Benchmark runner script
- Report generator (TCWP → HTML/MD)
- Hosted benchmark page (Vercel)

### Automation
```bash
# Run benchmark suite
./scripts/run-benchmark-suite.sh --workflows profile_edit,login,search --n 5

# Generate report
./scripts/generate-benchmark-report.sh --run-ids run_001,run_002,run_003

# Publish to Vercel
./scripts/publish-benchmark.sh --report benchmark_2026_03_29.html
```

---

## 8. Success Criteria for Benchmarks

| Metric | Target |
|--------|--------|
| Token savings vs full crawl | >60% |
| Time savings vs full crawl | >40% |
| Cost savings vs BrowserStack equivalent | >80% |
| N=5 pass rate | >80% |
| N=10 pass rate | >70% |
| Compression ratio (if applicable) | >20% step reduction |
| Published benchmark pages | 2+ by week 8 |
| Inbound inquiries from benchmarks | 3+ |
