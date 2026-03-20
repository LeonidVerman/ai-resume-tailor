# AI Resume Tailoring System – Improvement Recommendations

## Scope
This document summarizes improvement opportunities identified across 10+ role samples analyzed during testing.

Focus:
- Resume tailoring quality
- Role alignment accuracy
- Consistency across runs

Excluded:
- Parsing/rendering (handled separately)
- LLM roundtrip architecture changes

---

## Key Themes Observed

1. Strong backend/system depth (consistent strength)
2. Weak product/customer alignment in product-driven roles
3. Limited adaptability to role type (IC vs Staff vs AI/platform)
4. Missing iteration / PoC / experimentation signals
5. Occasional over-indexing on distributed systems mechanisms

---

## Improvement Items

---

### 1. Product Awareness Gap

**Observed in:**
- Branch – Senior Backend Engineer (Engagement)
- Instacart – Engineering Manager
- Loopio – Staff Engineer

**Problem:**
Resume emphasizes backend mechanics but lacks product/user impact.

**Example issue:**
- No mention of user journeys, customer impact, or product collaboration

**Recommendation:**
Add product-aligned phrasing in experience bullets

**Implementation options:**
- Candidate prompt enhancement ✅
- General prompt enhancement ✅

---

### 2. Missing Iteration / PoC Signal

**Observed in:**
- Branch
- Change.org (AI Tools)
- Instacart

**Problem:**
JD emphasizes rapid iteration, but resume lacks signals

**Recommendation:**
Inject “iterative delivery”, “PoC”, “MVP” patterns where supported

**Implementation:**
- General prompt enhancement ✅

---

### 3. Weak Ownership / SLA Framing

**Observed in:**
- Branch
- Microsoft
- Benevity

**Problem:**
Ownership implied but not explicit

**Recommendation:**
Explicitly include:
- ownership of production systems
- SLA responsibility
- lifecycle ownership

**Implementation:**
- General prompt enhancement ✅

---

### 4. Role-Type Adaptation Missing

**Observed in:**
- Change.org (AI/Tools)
- Instacart (Manager)
- Microsoft (IC)

**Problem:**
System applies same backend-heavy framing across different role types

**Recommendation:**
Introduce role-type awareness:
- Backend IC
- Product/backend hybrid
- AI/platform
- Manager/Staff

**Implementation:**
- Role prompt enhancement ✅

---

### 5. AI Positioning Underutilized

**Observed in:**
- Change.org (AI role)

**Problem:**
AI work framed as “evaluation” instead of “systems thinking”

**Recommendation:**
Reframe as:
- ambiguity handling
- workflow integration
- engineering productivity

**Implementation:**
- Candidate prompt enhancement ✅

---

### 6. Overuse of Distributed Systems Signals

**Observed in:**
- Multiple roles

**Problem:**
All resumes heavily emphasize:
- caching
- scaling
- async messaging

Even when not central to JD

**Recommendation:**
Conditionally include mechanisms only when relevant

**Implementation:**
- Candidate prompt refinement ✅

---

### 7. Weak Cross-Functional Signal

**Observed in:**
- Branch
- Instacart
- Loopio

**Problem:**
Limited mention of collaboration with:
- product
- stakeholders

**Recommendation:**
Add lightweight collaboration bullets

**Implementation:**
- General prompt enhancement ✅

---

### 8. Influence / Persuasion Missing

**Observed in:**
- Branch
- Staff roles

**Problem:**
No signal of:
- technical influence
- decision-making

**Recommendation:**
Add:
- “influenced technical decisions”
- “advocated tradeoffs”

**Implementation:**
- Role prompt enhancement ✅

---

### 9. Inconsistent Leadership Balance

**Observed in:**
- Manager vs IC roles

**Problem:**
Leadership sometimes too heavy or too weak

**Recommendation:**
Better enforce role-level tuning

**Implementation:**
- Role prompt enhancement ✅

---

### 10. Cover Letter Gaps

**Observed in:**
- Most roles

**Problem:**
- Strong technical alignment
- Weak company/product alignment

**Recommendation:**
Ensure:
- company-specific motivation
- product understanding

**Implementation:**
- General prompt enhancement ✅

---

## Model Consideration

### GPT-5.2 vs GPT-5.3

Observed:
- GPT-5.3 produces better structure and reasoning

Recommendation:
- Upgrade to GPT-5.3 for production

Impact:
- Medium to High improvement in consistency

---

## Summary of Improvement Levers

| Area | Approach |
|------|--------|
| Product awareness | Candidate + General prompt |
| Iteration / PoC | General prompt |
| Ownership framing | General prompt |
| Role-type adaptation | Role prompt |
| AI positioning | Candidate prompt |
| Mechanism overuse | Candidate prompt |
| Cross-functional | General prompt |
| Influence signal | Role prompt |
| Leadership balance | Role prompt |
| Cover letters | General prompt |
| Model quality | GPT-5.3 upgrade |

---

## Final Notes

System is already strong in:
- backend depth
- architecture articulation
- consistency

Next stage:
- contextual intelligence (role awareness)
- product alignment
- signal balance

These improvements will significantly increase hit rate across diverse roles.
