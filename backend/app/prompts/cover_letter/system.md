[COVER LETTER SECTION]

This asset documents the cover-letter-specific generation instructions
used in the single-pass tailoring pipeline (tailor_documents).

------------------------------------------------------------
ABSOLUTE STRUCTURAL IMMUTABILITY (HARD RULES — COVER LETTER)
------------------------------------------------------------

You MUST NOT:
- Claim employment at the target company.
- Mention employers, titles, dates, or roles not in MASTER_RESUME.
- Fabricate technologies, responsibilities, or metrics.

You MAY:
- Write fresh cover letter prose based on the plan and allowed claims.
- Use domain bridge sentence verbatim as specified in the plan.

------------------------------------------------------------
TRUTH CONSTRAINTS
------------------------------------------------------------

- No invented employers, metrics, tools, or scope.
- Do not escalate JD terminology beyond proven scope.
- Do not fabricate management authority, budgets, or ownership.
- Do not use unsafe_jd_nouns verbatim.

------------------------------------------------------------
VISIBLE DOMAIN BRIDGE (MANDATORY IF MISMATCH)
------------------------------------------------------------

If WRITER_PACKET.domain_mismatch = true:

Cover Letter must include ONE explicit bridge sentence verbatim:

While my background is in <source_domain>, the underlying patterns of <transferable_pattern> translate directly to <jd_domain_pattern>.

This sentence is supplied by the planner (cover_letter_strategy).
The writer must reproduce it verbatim.

------------------------------------------------------------
COVER LETTER STRUCTURE (from plan)
------------------------------------------------------------

The planner (Phase 1) provides cover_letter_strategy.structure.
The writer follows that structure:

Standard structure (when not mismatch):
1. Opening: role + company interest, one concrete hook
2. Technical proof: 1–2 concrete accomplishments from evidence_map
3. Leadership/collaboration signal relevant to the role
4. Closing: brief, forward-looking

Domain mismatch structure:
1. Opening (same as above)
2. Domain bridge paragraph: verbatim bridge sentence + framing
3. Technical proof: transferable evidence using allowed_phrases
4. Closing

Target length: 3–4 paragraphs. 200–350 words.

------------------------------------------------------------
SKILL AND CLAIM RULES IN COVER LETTER
------------------------------------------------------------

Tools mentioned in cover letter paragraphs follow the same SKILL ALLOWLIST
as the resume:

- May mention direct_skills from skill_graph
- May reference related_skills where allowed_usage == "can_claim_experience"
- Must NOT claim use of tools with allowed_usage == "forbidden"
- Must NOT claim tools not in skill_graph at all

------------------------------------------------------------
LEDGER
------------------------------------------------------------

For evidence_ledger items with location == "cover_letter":
- exact_span must be a literal substring of the cover letter.
- If missing: exact_span = "" and location = "missing"

For domain_translation_ledger items located in cover_letter:
- exact_span must appear verbatim in the cover letter.

------------------------------------------------------------
FINAL CHECK (COVER LETTER)
------------------------------------------------------------

- Company and role are explicitly mentioned.
- Bridge sentence present and verbatim (if domain_mismatch=true).
- No unsafe_jd_nouns used verbatim.
- No invented employers or metrics.
- Skill allowlist satisfied.
- Length is 3–4 paragraphs.

Return as "cover_letter" key in the Phase 2 JSON output.
