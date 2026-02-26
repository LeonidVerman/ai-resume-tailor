# Release Notes

## 0.1.1.APLHA — 2025-02-26

**Improved generation**

### New features
- **Density shortfall allowance** (`role_density_shortfall_allowance` in WriterPacket): when the master resume has fewer bullets than the density minimum for a high/medium priority role, the Phase 2 writer is now permitted to add up to 1 derived-but-grounded bullet per role (suppressed for thin roles and roles without plan evidence).
- **Updated Phase 2 prompts**: writer (v1.7) and repair (v3.1) prompts now include explicit guidance for the density shortfall allowance and a density preflight check. The repair prompt gains `TAILORING_PLAN` as an explicit input and a clearer support rule referencing `evidence_map`.

### Bug fixes
- **Abbreviated role-name matching** (`_roles_match`): Phase 1 sometimes abbreviates compound job titles (e.g. `"VP / Director of Software Development | Corp"` instead of `"VP / Director of Software Development / Lead Software Developer | Corp"`), causing `role_source_bullet_counts` and `role_source_char_counts` to return 0 for that role. A new pipe-part fallback matcher resolves this across all five matching callsites in `writer_packet.py` and `phase2_validator.py`.

### Tests
- 15 new deterministic tests (total: 157).

---

## 0.1.0.APLHA — 2025-02-25

**Initial version.**
