# 🚀 Next Features Roadmap (Based on Competitor Analysis)

## 🎯 Goal

Bridge the gap between:
- **Strong generation engine (already built)**
- **User-facing clarity, control, and trust**

Focus on:
- UX improvements
- Controlled customization
- Exposing internal strengths (evaluator, diff)

---

# 🔥 Tier 1 — MUST HAVE (High Impact, Low Effort)

## 1. Resume Diff View

### Why
- Builds trust in AI output
- Makes changes transparent
- Reduces fear of “AI hallucination”

### Implementation
- Side-by-side view:
  - Original vs Generated
- Highlight:
  - Added
  - Removed
  - Modified text

### Notes
- Data already exists in LLM JSON → UI work only

---

## 2. Generation Mode

### Modes

- **Conservative**
  - Strict truthfulness
  - Minimal rewriting
  - No aggressive keyword alignment

- **Balanced (default)**
  - Best mix of clarity + alignment

- **Aggressive**
  - Maximize ATS alignment
  - Still no hallucinations (must respect constraints)

### Implementation
- Add `generation_mode` parameter to pipeline
- Adjust:
  - mechanism density
  - rewrite aggressiveness
  - keyword alignment pressure

---

## 3. Evaluator UI (Expose Hidden Strength)

### Why
- Major differentiator vs competitors
- Converts “black box AI” → “measurable system”

### Display Metrics

- Role Fit
- Truthfulness
- Clarity / Impact
- Mechanism Quality
- Overall Readiness

### Optional
- Show score (1–10 or %)
- Show short explanation per metric

---

# 🧪 Tier 2 — HIGH VALUE

## 4. Customization Toggles

### Purpose
Lightweight control without breaking system integrity

### Toggles
- Emphasize metrics
- Emphasize leadership
- Emphasize architecture (key differentiator)

### Implementation
- Boolean flags passed into prompt
- Adjust weighting, not structure

---

## 5. ATS vs Human Indicator

### Simple Version

Display:
- ATS Alignment: High / Medium / Low
- Readability: Strong / Moderate / Weak

### Future
- Derived from evaluator scores

---

# ⏳ Tier 3 — OPTIONAL

## 6. Custom Instructions

### Purpose
Advanced user control

### Example
“Focus more on distributed systems and scalability”

### Constraints
- Must not override:
  - truthfulness
  - structure rules

---

## 7. Controlled Keyword Selection

### DO NOT
- Allow free keyword injection

### Instead
- Extract keywords from JD
- Let user select from:
  - validated skill pool
  - related expansions

---

# ❌ What NOT to Implement

## 1. Keyword Stuffing
- No inserting unsupported skills

## 2. Fake Skill Injection
- Never add technologies not present in source

## 3. Over-simplified Scoring
- Avoid “fake” ATS score logic
- Use real evaluator instead

---

# 🧠 Strategic Direction

## Current State
- Model-first system (strong backend, weak UX)

## Target State
- Model-first + UX clarity + user control

---

## Core Differentiator

> Evidence-based tailoring with zero hallucination tolerance

---

# 🧭 Implementation Order

1. Diff View
2. Generation Mode
3. Evaluator UI
4. Customization Toggles
5. ATS vs Human Indicator
6. Custom Instructions
7. Keyword Selection

---

# 💬 Final Note

This roadmap does NOT require major model changes.

Focus is:
- exposing existing strengths
- improving user trust
- adding controlled flexibility

