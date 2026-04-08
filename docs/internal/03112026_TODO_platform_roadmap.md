# retention.sh Platform Roadmap — March 11, 2026

> **One-liner**: *"We help you build and deploy your own in-house agents."*
>
> Anyone can build agents. We sell **reliability** and **enterprise-setting deployment**.

---

## Vision

The QA Bug Reproduction Agent is the **first showcase** of many agents the platform enables.
- **Khush** builds agents for robotic world model training data collection, organization, and auto-feedback federated learning.
- **Anyone else** who joins can build agents for their domain.
- Every agent built on the platform gets the same reliability guarantees: telemetry, guardrails, deployment pipeline, cost controls.

Everything we build traces back to three words in the one-liner:
- **BUILD** → Agent Template CLI + Registry + Versioning
- **DEPLOY** → Deployment Pipeline + Durable Sessions + Generic API
- **IN-HOUSE** → Multi-Tenant + RBAC + Cost Controls + Observability

---

## Layer 0: Foundation ✅ (Already Exists)

| Primitive | Location | Status |
|-----------|----------|--------|
| Agent Factories | `create_*_agent()` in coordinator/, qa_emulation/, device_testing/ | ✅ Solid pattern |
| Skill System | `orchestration/skills/` — metadata.yaml + SKILL.md + progressive disclosure | ✅ 4 skills |
| Model Tiering | `model_fallback.py` — GPT-5.4 / GPT-5.3-Codex / GPT-5-mini | ✅ March 2026 |
| Orchestration Session | `run_session.py` — state machine, inline LLM eval, retry | ✅ Production-quality |
| Telemetry | `RunTelemetry` + `MODEL_PRICING` in qa_emulation | ✅ Works (single agent) |
| Cloud Providers | `cloud_providers/factory.py` — local/Genymotion/AWS/BrowserStack | ✅ Good abstraction |
| API Layer | FastAPI with WebSocket, SSE, REST | ✅ Functional |

---

## Layer 1: Platform Core 🔴 (Must Build)

### 1.1 Agent Registry & Discovery
- **Priority**: CRITICAL
- **Problem**: Adding a new agent requires editing `coordinator_service.py` (2,040 lines). Agents are hardcoded imports.
- **Solution**: Central `AgentRegistry` class where agents self-register with metadata (name, version, capabilities, model requirements, cost profile, owner). Expose via `GET /api/agents`.
- **Pattern**: Extend `ProgressiveDisclosureLoader` to discover agent modules, not just skills.

### 1.2 Agent Template / Scaffold CLI
- **Priority**: CRITICAL — #1 onboarding bottleneck
- **Problem**: Creating a new agent requires reading 5+ files to understand the pattern.
- **Solution**: `python -m retention scaffold agent --name data_collection --owner khush`
- **Generates**: `agent_factory.py`, `metadata.yaml`, `SKILL.md`, `tools/`, `models/`, `tests/`
- **Unblocks**: Khush, future hires, anyone who wants to build agents.

### 1.3 Unified Telemetry Service
- **Priority**: HIGH
- **Problem**: `RunTelemetry` + `MODEL_PRICING` lives only in `qa_emulation/models/verdict_models.py`.
- **Solution**: Lift to platform-level service. Every agent gets automatic cost tracking via decorator/middleware on `Runner.run()`.
- **Fields**: agent_name, run_id, input_tokens, output_tokens, reasoning_tokens, cost_usd, latency_ms, model_used, success/failure.

### 1.4 Durable Session Backend
- **Priority**: HIGH
- **Problem**: In-memory `_sessions_storage` (agent_sessions.py line 18). Not production-ready.
- **Solution**: SQLAlchemy backend. OpenAI Agents SDK supports `SQLAlchemySession` natively.

### 1.5 Generic Agent Execution API
- **Priority**: MEDIUM
- **Solution**: `POST /api/agents/{agent_id}/run` — accepts any registered agent, routes to factory, returns result + telemetry.
- **Includes**: SSE streaming, WebSocket for real-time updates, run history.

### 1.6 Agent Versioning & Prompt Management
- **Priority**: MEDIUM
- **Solution**: Each agent registers prompt variants. Platform tracks which version produced which results. A/B comparison built-in.

---

## Layer 2: Enterprise Differentiators 🟡

| Feature | SDK Support | Status | Enterprise Value |
|---------|-------------|--------|------------------|
| **2.1 Guardrails** | Native (input/output/tool) | Not built | Prevent hallucinated verdicts, unsafe actions |
| **2.2 HITL Approvals** | Native (pause/approve/resume) | Not built | Required for production deployments |
| **2.3 Multi-Tenant** | Custom | Not built | Team isolation: Khush's agents ≠ QA agents |
| **2.4 Deployment Pipeline** | Custom | Not built | Individual agent deploy without full redeploy |
| **2.5 Observability** | LangSmith (partial) | Partial | Per-agent dashboards, alerting, SLA monitoring |
| **2.6 Eval Harness** | Custom (.eval.yaml) | Designed | Powers the "reliability" claim |
| **2.7 Cost Controls** | Custom | Basic | Per-agent spending limits, budget alerts |

---

## Layer 3: Agent Catalog & Go-to-Market 🟢

### Agents We Can Build & Showcase

| Agent | Status | Industry | Human Cost | Agent Cost | Savings |
|-------|--------|----------|------------|------------|---------|
| 🧪 QA Bug Reproduction | **LIVE** | Any | $100-320/bug | ~$0.50-2/run | **99%** |
| 📱 Mobile App Testing | Partial | Mobile-first | $80-160/suite | ~$1-5/suite | **97%** |
| 🎨 Design-to-Test (Figma) | Partial | Product teams | $200-400/review | ~$2-8/analysis | **98%** |
| 🤖 Robotics Training Data | Planned | Robotics/IoT | $50-100/hr labeling | ~$0.10-1/batch | **99%** |
| 📊 Data Pipeline Monitor | Template | FinTech, Health | $120K/yr SRE | ~$500-2K/mo | **85%** |
| 🛡️ Security Compliance | Template | Enterprise | $150-300/hr auditor | ~$5-20/scan | **95%** |
| 📞 Support Triage | Template | SaaS | $45K/yr L1 support | ~$200-800/mo | **80%** |
| 🚀 CI/CD Pipeline | Template | DevOps | $140K/yr DevOps | ~$300-1K/mo | **90%** |

**Cost basis**: GPT-5.4 @ $2.50/$15.00 per 1M tokens. GPT-5-mini @ $0.25/$1.00 per 1M tokens.



### Target Industries

| Industry | Pain Point | Entry Agent | Expansion |
|----------|-----------|-------------|-----------|
| **Enterprise QA** | Bug reproduction, regression | QA Agent (LIVE) | Mobile + Design-to-Test |
| **FinTech** | Compliance audits, transaction monitoring | Compliance Agent | Data Pipeline Monitor |
| **HealthTech** | HIPAA compliance, clinical data QA | Security Agent | QA + Data Pipeline |
| **E-commerce** | Mobile testing, support volume | Mobile Testing Agent | Support Triage |
| **SaaS** | CI/CD reliability, customer support | CI/CD Agent | Support Triage |
| **Robotics/IoT** | Training data, model feedback | Robotics Data Agent | Data Pipeline Monitor |

### Platform Capabilities vs Alternatives

| Capability | retention.sh | Build from Scratch | LangChain | CrewAI |
|------------|-----------|-------------------|-----------|--------|
| Agent Registry | ✅ (planned) | ❌ DIY | ❌ | ❌ |
| Unified Telemetry + Cost | ✅ (partial) | ❌ DIY | ❌ | ❌ |
| Guardrails (input/output/tool) | ✅ (planned) | ❌ DIY | Partial | ❌ |
| HITL Approvals | ✅ (planned) | ❌ DIY | ❌ | ❌ |
| Multi-Tenant RBAC | ✅ (planned) | ❌ DIY | ❌ | ❌ |
| Eval Harness (.eval.yaml) | ✅ (planned) | ❌ DIY | ❌ | ❌ |
| Progressive Disclosure | ✅ Built | ❌ | ❌ | ❌ |
| Model Fallback Chains | ✅ Built | ❌ DIY | Partial | ❌ |
| Mobile Device Control | ✅ Built | ❌ DIY | ❌ | ❌ |
| Vision-Augmented Navigation | ✅ Built | ❌ DIY | ❌ | ❌ |

---

## Recommended Build Order

### Phase 1 — NOW (2 weeks)
- [ ] **1.1 Agent Registry** ← unblocks everything
- [ ] **1.2 Agent Template CLI** ← unblocks Khush and new hires
- [ ] **1.3 Unified Telemetry** ← powers cost savings pitch

### Phase 2 — Month 2
- [ ] **1.4 Durable Sessions** ← production-ready
- [ ] **1.5 Generic Agent API** ← unified execution
- [ ] **2.1 Guardrails** ← enterprise trust
- [ ] **2.6 Eval Harness** ← reliability proof

### Phase 3 — Month 3
- [ ] **2.2 HITL Approvals** ← enterprise safety
- [ ] **2.3 Multi-Tenant** ← team scaling
- [ ] **2.5 Observability Dashboard** ← ops confidence
- [ ] **3.3 Cost Calculator UI** ← sales tool

### Phase 4 — Ongoing
- [ ] New agent templates per industry
- [ ] **2.4 Deployment Pipeline**
- [ ] **2.7 Rate Limiting & Cost Controls**

---

## Files Changed in This Branch (homen-shum-mar2026)

### March 2026 Model Migration
- `backend/app/agents/model_fallback.py` — GPT-5.4 → GPT-5.4, GPT-5.4-Codex → GPT-5.3-Codex
- `backend/app/agents/coordinator/coordinator_agent.py` — Updated model references
- `backend/app/agents/coordinator/coordinator_service.py` — Explicit reasoning_effort="high" at call sites
- `backend/app/agents/orchestration/evaluators.py` — Fallback to GPT-5.4
- `backend/app/agents/orchestration/progressive_disclosure.py` — Fallback to GPT-5.4
- `backend/app/e2e/verifier.py` — Fallback to GPT-5.4

### QA Emulation Module (New)
- `backend/app/agents/qa_emulation/` — Full module: service, agent factory, subagents, models
- `backend/app/agents/qa_emulation/models/verdict_models.py` — QAReproVerdict, RunTelemetry, MODEL_PRICING, configurable reasoning_effort
- `backend/app/agents/qa_emulation/qa_emulation_service.py` — Orchestrator with telemetry rollup
- `backend/app/agents/qa_emulation/subagents/` — Bug detection, anomaly detection, verdict assembly

### Tests
- `backend/app/agents/tests/test_coordinator_agent_model.py` — Updated for GPT-5.4
- `backend/app/agents/tests/test_model_fallback.py` — Updated for March 2026 models

### Skill Metadata
- `backend/app/agents/orchestration/skills/qa_emulation/` — metadata.yaml + SKILL.md