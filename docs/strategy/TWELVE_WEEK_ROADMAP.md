# retention.sh: 12-Week Roadmap

*3-month runway plan. Start: 2026-03-29. End: 2026-06-21.*

---

## Phase 1: Ship + Prove (Weeks 1-2)
**2026-03-29 to 2026-04-12**

### Deliverables
- [ ] Cloud dashboard live and stable (Vercel deployment)
- [ ] Trajectory replay visible in dashboard (saved path + checkpoint status)
- [ ] Before/after comparison view (baseline vs replay metrics)
- [ ] Savings dashboard (tokens, time, requests, cost delta)
- [ ] TCWP package format adopted internally (all runs emit TCWP bundles)
- [ ] Starter MCP workflow packaged (install in <5 min)
- [ ] One flagship demo flow recorded and shareable

### KPIs
| Metric | Target |
|--------|--------|
| Dashboard uptime | >95% |
| TCWP bundles generated | 10+ |
| Internal replay workflows | 3+ |
| Demo video published | 1 |

---

## Phase 2: Onboard + Benchmark (Weeks 3-6)
**2026-04-12 to 2026-05-10**

### Deliverables
- [ ] First 2-3 design partners onboarded
- [ ] BrowserStack comparison benchmark run and published
- [ ] N=5 and N=10 durability results on 2+ workflows
- [ ] ROI report template (auto-generated from TCWP sales_brief)
- [ ] One shareable case study published
- [ ] Workflow compression v1 (step elimination + audit)
- [ ] Local-to-cloud trajectory sync (upload local TCWP to dashboard)
- [ ] GitHub starter repo published (schemas + examples + CLI)

### KPIs
| Metric | Target |
|--------|--------|
| External users (active) | 5-10 |
| Design partners engaged | 2-3 |
| Published benchmark pages | 2+ |
| Case studies | 1 |
| Token savings demonstrated | >60% on 2+ workflows |
| N=10 pass rate | >80% |

---

## Phase 3: Convert + Deepen (Weeks 7-12)
**2026-05-10 to 2026-06-21**

### Deliverables
- [ ] First paid pilot closed ($5K+)
- [ ] 2-3 more pilots in pipeline
- [ ] Trajectory audit engine (propose shortcut, verify, approve)
- [ ] Optimization candidate dashboard (show verified vs pending shortcuts)
- [ ] Workflow compression v2 (parallel execution, state jumps)
- [ ] Team view in dashboard (multi-user workflows)
- [ ] Weekly savings digest email/Slack for design partners
- [ ] Decide deeper direction: mobile/browser ops vs enterprise workflow layer
- [ ] TCWP v1.1 schema (incorporate partner feedback)

### KPIs
| Metric | Target |
|--------|--------|
| Paid pilots | 1 closed, 2-3 in pipeline |
| Revenue | $5K-$15K |
| Weekly repeat users | 3+ |
| Workflows with >5 replays | 5+ |
| Average token savings | >70% |
| Shortcut audit throughput | 5+ candidates audited |

---

## Weekly Cadence

| Day | Activity |
|-----|----------|
| Monday | Sprint planning, priority review |
| Tuesday-Thursday | Build + ship |
| Friday | Demo, benchmark run, metrics review |
| Saturday | Community + distribution (GitHub, docs, social) |
| Sunday | Automated regression + health checks |

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Dashboard ships late | Cut scope to replay + savings only, add features incrementally |
| No external users | Leverage friends/community first, lower friction further |
| Pilot conversion stalls | Focus on proving savings on customer's own workflow, not demos |
| Trajectory drift too high | Improve checkpoint validation, add auto-recovery logic |
| Team bandwidth | Strict 20/80 allocation, cut side quests aggressively |
| Competitor copies open tools | Moat is in trajectory memory + compression, not tools |

---

## Decision Points

### Week 4 checkpoint
- Are external users running workflows weekly? If no, refocus on adoption friction.
- Is the savings story clear and resonating? If no, refine messaging.

### Week 8 checkpoint
- Is anyone willing to pay? If no, pivot pilot structure or ICP.
- Are replay economics holding? If no, debug cost model.

### Week 12 checkpoint
- Close assessment: Are we on track for sustainable revenue path?
- Direction decision: mobile/browser ops vs enterprise workflow layer.

---

## Exit Criteria (End of 12 Weeks)

**Success:** 1 paid pilot, 5+ active users, 2+ published benchmarks, clear revenue path.
**Survival:** 3+ design partners, clear savings proof, pipeline building but no revenue yet.
**Failure:** No external users, no savings proof, no pipeline, team misaligned on identity.
