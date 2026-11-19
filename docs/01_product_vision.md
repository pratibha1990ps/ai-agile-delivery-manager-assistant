# 01 · Product Vision (Idea Stage)

> **Product:** AI Agile Delivery Manager Assistant
> **Type:** an independent portfolio project using **synthetic data only**
> **Stage owner:** the product owner and builder, working with an AI pair programmer

## 1. Problem statement

On an enterprise Agile programme, a Delivery Manager has to piece together the true state of delivery from several sources:

- RAID logs (risks, assumptions, issues, actions and decisions);
- sprint reports, PI objectives, dependency trackers and milestone plans.

Doing this by hand is slow and inconsistent, and it is easy to miss things. Overdue actions, stale risks, and dependencies that are quietly slipping all get lost in the noise.

Generative AI could summarise and interpret this data. In delivery reporting, though, a **hallucinated fact is worse than no fact**. An invented risk, a wrong date or a made-up status damages trust with leadership straight away.

**Problem in one line:** Delivery Managers need faster, consistent insight into programme health without giving up the factual accuracy and traceability that governance requires.

## 2. Target personas

| Persona | Needs | Pain today |
|---|---|---|
| **Delivery Manager** (primary) | One view of programme health; RAID hygiene; weekly status report | Hours spent collating spreadsheets; inconsistent RAG judgements |
| **Programme / Portfolio Lead** | A trustworthy executive summary and escalations | Reports are late, subjective or hard to verify |
| **Scrum Master / Squad Lead** | Their squad's predictability, scope change and blockers | Metrics are scattered across tools |
| **Hiring Manager** (portfolio audience) | Evidence of delivery expertise and responsible AI engineering | Generic AI demos with no governance or testing |

## 3. Value proposition

**"Rules compute, AI interprets."**

| For the Delivery Manager | Because |
|---|---|
| Programme health in seconds | Deterministic rules calculate the metrics and RAG statuses from source data |
| Insight they can defend | Every AI statement cites source record IDs and is validated before it is shown |
| Less reporting effort | The AI drafts the leadership report; a human edits and approves it |
| Better decisions | A what-if calculator plus AI option analysis, logged to a decision register |

## 4. Product goals

| # | Goal | Delivered by |
|---|---|---|
| G1 | Show programme health across five squads at a glance | Delivery Control Tower |
| G2 | Find RAID hygiene problems and patterns across squads | AI RAID Analyst |
| G3 | Measure sprint and PI predictability objectively | Sprint & PI Health Analyser |
| G4 | Produce grounded, editable leadership reports | AI Leadership Reporting |
| G5 | Support delivery decisions with evidence | Delivery Decision Assistant |
| G6 | Show responsible AI: no invented facts, full traceability | Fact packs, grounding validator, audit log |

## 5. Non-goals and constraints

| Type | Statement |
|---|---|
| Constraint | **Synthetic but realistic data only.** No real client, company or employer data |
| Constraint | **No connection to employer systems** or corporate tools |
| Constraint | **No paid enterprise software or integrations.** Free, open-source stack |
| Constraint | The LLM is **configured separately** from the application, and the app must run with no LLM key at all |
| Constraint | Start with a **simple working MVP**, with a modular and testable architecture |
| Non-goal | Multi-user authentication, role-based access or production hosting |
| Non-goal | Live Jira, Azure DevOps or ServiceNow integrations. CSV/Excel import stands in for them |
| Non-goal | AI assigning RAG statuses or calculating metrics |
| Non-goal | PDF export in the MVP. Markdown and DOCX only |

## 6. Success measures

| Measure | Target | How it's checked |
|---|---|---|
| Factual grounding | 100% of displayed AI findings carry valid citations | Grounding validator tests; AI audit log |
| Rule correctness | Every planted synthetic scenario is detected; no false positives on the control squad | Scenario (golden) tests |
| Rules test coverage | ≥ 90% on `src/adm/rules/` | pytest coverage |
| Zero-cost run | The app and the full test suite run with no API key | CI on the mock provider |
| Setup time | A reviewer runs the app in under 10 minutes | README quick start |
| Portfolio clarity | Vision → discovery → design → backlog → evidence documented | This `docs/` folder |
