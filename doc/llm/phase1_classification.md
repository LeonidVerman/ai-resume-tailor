Implement Phase 1 of upload-time resume template classification for CVRocket.

Goal:
Add a one-time LLM classification step after resume upload that produces a structured semantic classification of the uploaded resume template.

This phase should:
- NOT change current tailoring behavior
- NOT change current rendering behavior
- NOT introduce validation/recovery yet
- simply produce and persist classification output so we can inspect and test it extensively

High-level architecture

Current idea:
1. Resume is uploaded
2. Parser extracts structural data from the template
3. A new LLM classification call is made once per uploaded resume
4. The classifier returns structured JSON describing:
   - sections
   - roles
   - paragraphs/blocks
   - semantic types
   - rewrite policies
5. That classification is stored for later use
6. No existing tailoring/rendering flow should depend on this yet

Important constraints

- Clean structure
- UTF-8 only
- No unnecessary refactors
- Keep this phase additive and low-risk
- Classification should be inspectable/debuggable
- Existing functionality must continue to work unchanged

Implementation requirements

1. Add parser-assigned stable IDs to the parser output used for classification
At minimum ensure there are stable IDs for:
- document_id
- section_id
- para_id
- role_id where roles exist

If some of these already exist, reuse them.
Do not rely on raw text as identity.
If role_id currently depends on normalized header text, replace or supplement it with a stable synthetic ID.

2. Add a classification input model
Create a lightweight structured model used as input to the classifier LLM.
This should be derived from parsed resume/template structure and include only the information needed for classification.

Suggested shape:
- document_id
- source_kind
- sections[]
  - section_id
  - raw_title
  - paragraphs[]
    - para_id
    - text
    - parser_semantic
  - roles[]
    - role_id
    - header_para_ids[]
    - meta_para_ids[]
    - bullet_para_ids[]

Keep this model separate from current rich IR if that makes implementation cleaner.

3. Add a classification output model
Implement a typed model / schema matching the classification JSON format.

Include:
- document_id
- classification_version
- source_kind
- sections[]
  - section_id
  - raw_title
  - display_title
  - semantic_type
  - rewrite_policy
  - preserve_heading
  - preserve_body_structure
  - blocks[] for non-experience sections
  - roles[] for experience sections
    - role_id
    - header_blocks[]
    - meta_blocks[]
    - body_blocks[]

Block fields:
- block_id
- para_id
- semantic_type
- rewrite_policy
- new

4. Add LLM classification prompt
Create a dedicated prompt file for upload-time classification, for example something like:

prompts/classification/classify_template_resume.txt

The prompt should:
- classify structure only
- return JSON only
- preserve IDs
- use the fixed semantic/rewrite policy enums
- be conservative for uncertain cases

Do not inline the prompt in code unless there is a very strong reason.

5. Add LLM classification call
Implement a new service/function that:
- accepts the parser-produced classification input
- calls the model with the classification prompt
- parses the JSON response into the classification output model
- returns/stores the classification artifact

This call should happen once after upload, not during every generation.

6. Persist classification result
Persist the classification output so it can be inspected later.
Use the cleanest existing storage approach consistent with the project.

Good options:
- add a new DB column/field associated with uploaded resume/template
- or add a new structured artifact record
- or equivalent existing persistence mechanism

The important part is:
- it should be retrievable later
- it should be tied to the uploaded resume/template version
- it should not overwrite existing IR data

7. Add inspection/debug visibility
Add a minimal way to inspect the stored classification result for testing/debugging.
This can be:
- admin/debug endpoint
- internal API endpoint
- admin page panel
- JSON download
- equivalent simple mechanism

This does not need full UX polish yet.
It just needs to make classification output visible and testable.

8. Keep current functionality unchanged
Important:
- current tailoring flow should continue to use the current logic
- current render/docx/pdf flow should remain unchanged
- classification should be computed and stored, but not yet used to drive document rewriting

9. Prepare for later integration
Structure the implementation so later phases can consume classification cleanly.
That means:
- avoid mixing classification logic into rendering code now
- keep models/services modular
- make it easy for later phases to enrich IR from classification

Suggested implementation approach

A. Parser side
- ensure stable IDs exist
- derive a compact classification input payload from parsed structure

B. LLM side
- prompt file for classification
- typed response parsing

C. Persistence side
- store classification result next to uploaded template / structured resume record

D. Debug side
- expose stored classification for inspection

Deliverables

Please implement:
1. Classification input model
2. Classification output model
3. Prompt file
4. LLM classification service/function
5. Persistence of classification result
6. Minimal retrieval/debug visibility

Do not implement yet:
- validation/recovery loop
- retry on malformed structure
- tailoring integration
- renderer integration
- use of classification in updater logic

Acceptance criteria

- Uploading a resume triggers one classification call
- The classification call uses parser-produced structured input with stable IDs
- The model returns structured JSON matching the classification schema
- The classification result is stored and retrievable
- Current resume generation behavior remains unchanged
- Classification output can be inspected for real uploaded resumes
- Code is modular and ready for next-phase validation/integration

Please inspect the existing code paths first, especially:
- upload flow
- parser flow / IR creation
- where resume/template metadata is stored
- existing prompt loading pattern
- current LLM integration utilities

Then implement the minimal clean additive version of this feature.