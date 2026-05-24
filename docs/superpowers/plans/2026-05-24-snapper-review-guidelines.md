# Snapper Review Guidelines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace stubs in `guides/snapper/` with a comprehensive set of review instructions (~73 rules across cross-cutting + 6 subsystem files), driven by `draft-rules.md`, `README-Devel.md`, and codebase analysis.

**Architecture:** Content-authoring plan — no code changes. Cross-cutting rules go in one rewritten `technical-patterns.md` (9 sections, ~43 rules). Subsystem-scoped rules go in 6 subsystem files (~30 rules). New `false-positive-guide.md` codifies the don't-flag list. `skills/snapper.md` and `slash-commands/snapper-review.md` expand to orient the reviewer. `draft-rules.md` is preserved with an archival header. `guides/smartnic-snapper/` (duplicate stub dir) is deleted.

**Tech Stack:** Markdown content. Verification via `bb_review.guidelines.load_rich_context()` (existing loader) — confirms the new files are picked up and the trigger table parses correctly.

**Sources (read at the start of each content task):**
- Spec: `docs/superpowers/specs/2026-05-24-snapper-review-guidelines-design.md`
- Draft rules: `guides/snapper/draft-rules.md` (provenance for most rule examples)
- README-Devel: `/home/kostik/repos/amd-smartnic-snapper/README-Devel.md` (MUSTs for naming, Doxygen, verdicts, headers)
- Sibling reference: `guides/swamp/` has a `false-positive-guide.md` and a polished `technical-patterns.md` — use as structural reference for tone and formatting.

---

## Task 1: Cleanup — delete duplicate dir, archive draft-rules

**Files:**
- Delete: `guides/smartnic-snapper/` (entire directory; verified earlier as a stub duplicate of `guides/snapper/` minus `draft-rules.md`)
- Modify: `guides/snapper/draft-rules.md` (add archival header at the top)

- [ ] **Step 1: Verify the dir is safe to delete**

Run: `diff -r guides/snapper guides/smartnic-snapper`
Expected: only differences are the `name:` frontmatter field in skill files and the absence of `draft-rules.md` in smartnic-snapper. Nothing valuable lost.

- [ ] **Step 2: Delete the duplicate directory**

Run: `rm -rf guides/smartnic-snapper/`

- [ ] **Step 3: Add archival header to `draft-rules.md`**

Edit `guides/snapper/draft-rules.md`, inserting this block immediately after the existing `# Draft Review Rules: snapper` heading and before the first rule:

```markdown
> **ARCHIVAL — DO NOT LOAD FOR REVIEWS.**
>
> This document is the original flat draft from which the structured snapper
> review guidelines were derived (see `technical-patterns.md`, `subsystem/`,
> and `false-positive-guide.md`). It is preserved for provenance and
> historical reference only. The review protocol does not load it. If a rule
> here disagrees with the structured guides, the structured guides win.
>
> See `docs/superpowers/specs/2026-05-24-snapper-review-guidelines-design.md`
> for the reorganization rationale.
```

- [ ] **Step 4: Verify the archival header is in place**

Run: `head -15 guides/snapper/draft-rules.md`
Expected: shows the `# Draft Review Rules: snapper` heading followed by the ARCHIVAL block.

- [ ] **Step 5: Commit**

```bash
git add -A guides/snapper/draft-rules.md guides/smartnic-snapper
git commit -m "docs(snapper): archive draft-rules.md, drop duplicate smartnic-snapper dir"
```

---

## Task 2: Rewrite `technical-patterns.md` (cross-cutting rules)

**Files:**
- Modify (full rewrite): `guides/snapper/technical-patterns.md` (currently 1.6KB stub, will be ~12-15KB)

**Source material:**
- Spec section "`technical-patterns.md` — section-by-section content" (sections A through I)
- Provenance for each rule:
  - Section A (Naming/identifiers): draft-rules.md rules "Symbol Prefix Discipline", "Spelling In Identifiers...", "Copyright Header: Current Year...", "Reuse Existing Helpers Instead Of Re-Implementing"; README-Devel.md lines 82–186 for function-name scope rule.
  - Section B (Comments/documentation): draft-rules.md "Doxygen Comments On Every Exported API"; README-Devel.md lines 230–693 for the full Doxygen rules.
  - Section C (File-level): draft-rules.md "Copyright Header...", "Header Include Order", "Local Helper Functions Must Be static", "`extern` In `.c` Files...".
  - Section D (Control flow): draft-rules.md "NULL Pointer Comparisons Must Be Explicit", "`if`/`else` Brace Symmetry", "Pointless `break` / `default:`-Only Switches", "Replace `while (1)` ... With `for`", "Loop Bound Should Use The Actually-Filled Count", "Blank Line After Variable Declarations", "Avoid Excessive `#if`/`#endif`...".
  - Section E (Constants/types): draft-rules.md "Magic Numbers In Code And Variant Defaults", "Format Specifiers For Typedef'd Integers", "Boolean-Returning Functions Should Return `bool`", "Prefer Enums Over Bare `bool`...", "`const char*` Return From `*_to_str`...", "Error Code Literals Should Be Named".
  - Section F (Memory): draft-rules.md "`eftest_malloc()` Must Be Paired With Free, And `mmap` Must Check `MAP_FAILED`".
  - Section G (API design): draft-rules.md "Out Parameters: Naming And NULL-Handling".
  - Section H (Logging/verdicts): draft-rules.md "`step()` / `log()` / Verdict Strings Must Not Contain `\n`", "Verdict Names: Namespaced And Unique", "Do Not Use `__func__` In Log Messages", "Log Lines Must Not Begin With Whitespace", "Iteration Indices In `step` / Verdict Strings", "Don't Re-Log Errors That Callees Already Logged"; plus README-Devel.md lines 196–228 for verdict naming.
  - Section I (Commit/review): draft-rules.md "Patch Scope: One Logical Change Per Commit", "Commit Description Quality", "`FIXME` / Workaround Tags Must Reference A Real Ticket", "Unrelated Reformatting Inside A Feature Patch".

- [ ] **Step 1: Read the source documents**

Run: `cat docs/superpowers/specs/2026-05-24-snapper-review-guidelines-design.md guides/snapper/draft-rules.md | wc -l`
Expected: confirms both files exist; total ~1100 lines of source material.

Also read `guides/swamp/technical-patterns.md` to see the structural conventions used in a sibling polished file (headings, code-block style, "Bug:" vs correct-pattern formatting).

- [ ] **Step 2: Write the full `technical-patterns.md` file**

Use the structure below. For each rule, copy the rule statement from the spec, then carry over the correct-pattern and "Bug:" code examples from the corresponding draft-rules.md section. Where the spec adds rules not in draft-rules.md (Doxygen detail, file headers, function-name scope rule), draw the example from README-Devel.md (cite the line range in the rule body).

File structure:

```markdown
# Snapper Review Patterns

> **Legacy-code pragma:** roughly 75% of this codebase predates the
> conventions below (README-Devel.md line 84). Apply these rules to
> **new files and modified lines only**. Do not reject a patch for
> legacy style it did not touch.

## A. Naming and identifiers

### Symbol prefix discipline
[rule statement + correct pattern code block + Bug: code block]

### Function-name scope rule
[...]

### Spelling in identifiers, comments, and strings
[...]

### AMD-not-Xilinx in new files
[...]

### Reuse existing helpers; don't re-implement
[...]

## B. Comments and documentation
[seven subsections]

## C. File-level conventions
[three subsections]

## D. Control flow and structure
[seven subsections]

## E. Constants and types
[six subsections]

## F. Memory and resources
[two subsections]

## G. API design (cross-cutting)
[two subsections]

## H. Logging, steps, verdicts
[seven subsections]

## I. Commit / review process
[four subsections]
```

For each subsection use this format (matching `guides/swamp/technical-patterns.md` style):

```markdown
### <Rule title>

<One- to two-sentence rule statement.>

\`\`\`c
<correct-pattern code block from draft-rules.md or README-Devel.md>
\`\`\`

**Bug: <short bug label>**

\`\`\`c
<anti-pattern code block from draft-rules.md>
\`\`\`
```

For rules without a clean correct/bug pair in draft-rules.md (e.g. Doxygen detail rules from README-Devel.md), it's OK to have only a correct pattern. For rules that are pure process (commit message quality, patch scope), it's OK to have no code block — just a clear rule statement and a one-line "what to flag" pointer.

- [ ] **Step 3: Verify rule count and structure**

Run: `grep -c "^### " guides/snapper/technical-patterns.md`
Expected: 43 (matches the spec's count: 5+7+3+7+6+2+2+7+4).

Run: `grep -c "^## " guides/snapper/technical-patterns.md`
Expected: 9 (sections A through I).

- [ ] **Step 4: Placeholder scan**

Run: `grep -nE "TBD|TODO|FIXME|fill in|XXX|placeholder" guides/snapper/technical-patterns.md`
Expected: no output, or only matches inside code-block examples (e.g. the `FIXME` rule itself has `FIXME_X4_5218_*` as an example — that's fine).

- [ ] **Step 5: Commit**

```bash
git add guides/snapper/technical-patterns.md
git commit -m "docs(snapper): rewrite technical-patterns.md with full rule set"
```

---

## Task 3: Write `false-positive-guide.md`

**Files:**
- Create: `guides/snapper/false-positive-guide.md`

**Source material:**
- Spec section "`false-positive-guide.md` content"
- Current `guides/snapper/technical-patterns.md` and `guides/snapper/skills/snapper.md` for the already-known false positives (indirect includes, local-fn arg validation, eftest_verdict_fail exit, missing-assertion-in-non-debug)
- Reference: `guides/swamp/false-positive-guide.md` for tone/structure

- [ ] **Step 1: Read the reference**

Run: `cat guides/swamp/false-positive-guide.md | head -60`
Expected: shows a section-per-pattern layout with "Pattern", "Why it's acceptable", "When it IS actually a problem" — use a similar layout.

- [ ] **Step 2: Write the file**

Create `guides/snapper/false-positive-guide.md` with this content shape:

```markdown
# Snapper False-Positive Guide

Patterns that look like issues but are intentional in this codebase. Do not
flag these. Each section gives the pattern, why it's accepted here, and the
narrow edge cases where it *is* a real bug.

## Indirect includes in `.c` files
[~3 paragraphs covering: convention via header chains, edge case: a
.c file that uses a symbol whose chain was severed by a refactor —
that's a real header-hygiene bug.]

## Static / local functions without argument validation
[convention: caller responsibility; edge case: a static function that
becomes externally callable later — at that point arg validation
becomes required.]

## Code after `eftest_verdict_fail*`
[these exit the process; not dead code; edge case: if the caller is
inside a function that takes a callback or returns a value, you may
still need a `return` to satisfy the compiler — that's a compile
issue, not a review one.]

## Missing assertions in non-debug build paths
[assertions are compiled out in release; behaviour-essential checks
must use `eftest_verdict_*` or explicit error returns; assertions are
for debug-only invariants.]

## Legacy code that predates current conventions
[~75% of the codebase per README-Devel.md line 84. Enforce style on
new files and modified lines only. Do not propose rewrites of
legacy style the patch did not touch. Edge case: if the patch
*moves* a legacy function (e.g. relocates to a new file), the moved
copy is "new code" in its new location and should follow current
conventions.]

## Existing `FIXME_*`-guarded workarounds
[don't flag a workaround that already has a `FIXME_*` macro AND a
ticket reference. Only flag NEW workarounds missing the tag, or
existing tags with a typo'd ticket number (e.g. `SNAP_11388` where
the real ticket is `SNAP-11338`).]

## Legacy `eftest_*` naming on existing symbols
[the naming migration to `ef_*` / `sl_*` is gradual per
README-Devel.md lines 88–127. Do not propose renames of legacy
`eftest_*` symbols. Only enforce the modern prefix on NEW symbols
the patch introduces.]

## Existing `#if WITH_X4` etc. on legacy code paths
[compile-time chip selectors that work today are not a bug.
`chip-dispatch.md` prefers runtime dispatch via `ef_vi_get_kind()`
for NEW code, especially in tests where both kinds may coexist in
one build. Don't propose refactoring working `#if WITH_*` blocks
unless the patch is already touching them.]
```

Fill out each section with 2-4 sentences. Don't paste large code examples — the focus here is "why it's OK" prose, not pattern recognition (that's `technical-patterns.md`'s job).

- [ ] **Step 3: Placeholder scan**

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/false-positive-guide.md`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add guides/snapper/false-positive-guide.md
git commit -m "docs(snapper): add false-positive-guide.md"
```

---

## Task 4: Write `subsystem/lib.md`

**Files:**
- Modify (full rewrite from TODO stub): `guides/snapper/subsystem/lib.md`

**Source material:**
- Spec section "`lib.md` — lib/ API design"
- Provenance: draft-rules.md rules "Internal Library APIs Must Stay Internal", "`eftest_func_*` / `eftest_efvi_*` Accessors Over Raw Field Access", "Pseudo-Header API Parity (TX vs RX)", "Common Inner Body For Wait/Process Completion Pairs", "Per-VI / Per-Kind Runtime Dispatch Over `#if WITH_EF_VI_X4_LL`", "Avoid Reinventing Per-Kind Logic In Stats Files", "`eftest_func_get_pf()` Helper Over Inline PF Lookup"

- [ ] **Step 1: Write the file**

Create with this structure (6 rules, one section each, same `### Title / rule / code / Bug:` format as `technical-patterns.md`):

```markdown
# lib/ Subsystem Guide

Rules for code in `src/lib/eftest/` and the public header tree
`src/include/ci/eftest/`. Load when the diff touches any of the
triggers listed in `subsystem/subsystem.md` under "lib APIs".

## Internal-vs-public header boundary
[Don't move types from src/lib/eftest/* (internal) into
src/include/ci/eftest/* (public) without justification. Internal
struct bodies like rxgen_ctx_t, rxgen_pkt_t, rxgen_rcpt_t stay
private. Tests should not depend on lib internals.]
- correct code block
- Bug code block (from draft-rules.md "Internal State Exposed" example)

## Prefer accessor helpers over raw field access
[eftest_func_get_pf, eftest_func_user, eftest_func_bdf,
eftest_func_kind, eftest_efvi_func. Don't inline func->user/func->bdf.]
- correct code block
- Bug code block (from draft-rules.md "Inline PF/VF Branching")

## Pseudo-header API parity (TX vs RX)
[eftest_efvi_rx_pseudo_hdr_size and tx_pseudo_hdr_size keep parameter
shape in sync. A patch updating one must update the other or
document why not.]

## Common inner body for wait/process completion pairs
[Extract sl_nvme_qpair_process_completion(qp, &cmpl) and have both
sl_nvme_qpair_wait_completion and sl_nvme_qpair_get_completion call it.]
- correct code block from draft-rules.md "Common Inner Body..."

## Per-VI / per-kind value lookup lives in lib, not tests
[Helpers like unsolicited_events_max_for_vi(vi) belong in lib.
Test-side #if WITH_EF_VI_* selectors are wrong shape. The helper
style itself is covered in chip-dispatch.md.]

## Per-kind dispatch in stats files
[New per-chip code in stats files (src/lib/eftest/stats/port_alerts.c
pattern) goes inside the existing switch(eftest_func_kind(func))
dispatcher, not scattered #if WITH_X4 blocks across the file.]
- correct code block from draft-rules.md "Avoid Reinventing Per-Kind..."
```

Fill each section with the rule statement (1-2 sentences) and the code blocks copied directly from the cited draft-rules.md sections.

- [ ] **Step 2: Placeholder scan and rule count**

Run: `grep -c "^## " guides/snapper/subsystem/lib.md`
Expected: 6.

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/subsystem/lib.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add guides/snapper/subsystem/lib.md
git commit -m "docs(snapper): write subsystem/lib.md"
```

---

## Task 5: Write `subsystem/mcdi.md`

**Files:**
- Create: `guides/snapper/subsystem/mcdi.md`

**Source material:**
- Spec section "`mcdi.md` — MCDI RPC wrappers"
- Provenance: draft-rules.md rules "MCDI `*_OUT_LEN` / `*_OUT_LENMIN` Constants", "`CI_BUILD_ASSERT` MCDI Enum / Snapper Enum Pairs", "Use `CI_MIN` To Clamp Buffer Copies"

- [ ] **Step 1: Write the file**

```markdown
# MCDI Subsystem Guide

Rules for code that wraps MCDI (Management Controller Driver Interface)
RPCs. Load when the diff touches `mcdi_rpc`, `ef_mcdi_*`,
`eftest_mcdi_*`, `MC_CMD_*`, or `MCDI_PAYLOAD*` symbols.

## Use `MC_CMD_..._OUT_LEN` / `..._OUT_LENMIN` / `..._OUT_LENMAX` macros
[Never hand-write byte sizes. mcdi_rpc(func, &req, 16, NULL) is wrong
when MC_CMD_TELEMETRY_READ_DATA_OUT_LEN exists.]
- correct + Bug code from draft-rules.md

## `CI_BUILD_ASSERT` every member of mirrored enum pairs
[When a snapper-level enum (eftest_*_t) mirrors an MCDI enum
(MC_CMD_..._OUT_* or TELEMETRY_EVENT_..._TYPE_*), require a
CI_BUILD_ASSERT for every member. Missing asserts are a real bug
source — caller-supplied enums silently drift from MCDI definitions.]
- correct code from draft-rules.md "CI_BUILD_ASSERT MCDI..."

## `CI_MIN` clamp payload copies against caller capacity
[memcpy of an MCDI payload into a caller buffer must clamp the count.
Don't trust server-provided length.]
- correct + Bug code from draft-rules.md "Use CI_MIN..."

## Blank line between `mcdi_rpc()` and the field-extraction block
[Visual separator. The rpc call and its arguments are one logical
unit; the extraction of fields from the reply is another.]
- correct code from draft-rules.md "MCDI ... Constants" (the example
showing `if( rc ) return rc;` followed by blank line then payload
extraction)
```

- [ ] **Step 2: Verify**

Run: `grep -c "^## " guides/snapper/subsystem/mcdi.md`
Expected: 4.

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/subsystem/mcdi.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add guides/snapper/subsystem/mcdi.md
git commit -m "docs(snapper): add subsystem/mcdi.md"
```

---

## Task 6: Write `subsystem/chip-dispatch.md`

**Files:**
- Create: `guides/snapper/subsystem/chip-dispatch.md`

**Source material:**
- Spec section "`chip-dispatch.md` — per-chip / per-kind dispatch"
- Provenance: draft-rules.md rules "Per-Chip / Per-Backend Dispatch Goes In A `query()` / `dispatch()` Helper", "Derived `WITH_*` Flags For Chip-Crossed Features", "Per-VI / Per-Kind Runtime Dispatch Over `#if WITH_EF_VI_X4_LL`"

- [ ] **Step 1: Write the file**

```markdown
# Chip-Dispatch Subsystem Guide

Rules for code that selects per-chip or per-VI-kind behaviour. Load
when the diff touches `WITH_X4`, `WITH_EF10`, `WITH_EF100`,
`WITH_EF_VI_*`, `eftest_func_kind`, `eftest_func_stage`, or
`ef_vi_get_kind`.

## Hoist long `switch (eftest_func_kind(func))` blocks into a `query()` helper
[When two or more sibling functions in the same file repeat the same
chip-switch, factor out a query()/dispatch() helper that returns the
right value/rc. Callers stay flat.]
- correct code from draft-rules.md "Per-Chip / Per-Backend Dispatch..."

## Derived `WITH_FEATURE_CHIP` flags for two-axis gating
[A feature gated by WITH_PORT_ALERTS && WITH_X4 simultaneously
should have a single WITH_PORT_ALERTS_X4 derived flag in the
appropriate config header, not nested #ifs at every use site.]
- correct + Bug code from draft-rules.md "Derived `WITH_*` Flags..."

## Runtime per-kind dispatch over compile-time `#if WITH_EF_VI_X4_LL`
[A snapper X4 build can have both WITH_EF_VI_X3 and WITH_EF_VI_X4_LL
defined simultaneously. Test-side #if/#elif selectors picking a
per-chip macro at compile time will choose the wrong value at
runtime. Use a runtime helper inspecting ef_vi_get_kind(vi).]
- correct code from draft-rules.md "Per-VI / Per-Kind Runtime..."

## Static `*_for_vi()` helper style for per-kind value lookup
[The helper itself: static, takes the VI, switches on
ef_vi_get_kind(), returns the per-kind value, calls
eftest_verdict_fail on an unknown kind.]
- correct code (the unsolicited_events_max_for_vi example from
draft-rules.md)
```

- [ ] **Step 2: Verify**

Run: `grep -c "^## " guides/snapper/subsystem/chip-dispatch.md`
Expected: 4.

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/subsystem/chip-dispatch.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add guides/snapper/subsystem/chip-dispatch.md
git commit -m "docs(snapper): add subsystem/chip-dispatch.md"
```

---

## Task 7: Write `subsystem/hardware-invariants.md`

**Files:**
- Create: `guides/snapper/subsystem/hardware-invariants.md`

**Source material:**
- Spec section "`hardware-invariants.md` — HW-touching correctness"
- Provenance: codebase analysis (Explore agent report); FLR rule also exists in draft-rules.md ("Reinit Dependent Resources After FLR / Function Reset")

This file is mostly NEW content from the codebase analysis. It does not have a direct draft-rules.md predecessor for the cosim/EVQ/descriptor-ring/byte-order rules — those come from the deep analysis surfaced during brainstorming. Be precise: cite the file paths from the analysis, mark these rules as CRITICAL or HIGH per the spec's severity table.

- [ ] **Step 1: Write the file**

```markdown
# Hardware-Invariants Subsystem Guide

Rules for correctness of HW-touching code: cosim DMA/IOMMU, event-queue
phase-bit tracking, descriptor ring producer/consumer arithmetic,
byte-order on descriptor encoding, and post-FLR resource reinit. Load
when the diff touches `src/lib/eftest/cosim.c`, `src/lib/eftest/ef10/`,
`src/lib/eftest/ef100/`, `src/lib/eftest/ef_vi/`, `event.c`,
descriptor-ring symbols, or `eftest_func_flr*`.

These rules describe a high-risk area: cosim mapping leaks, EVQ
phase-bit confusion, ring wraparound off-by-one, and stale resource
use after FLR have been recurring bug classes per the SNAP-11426
series and the X4-7602 lock-discipline fix. Treat findings here as
CRITICAL or HIGH per `slash-commands/snapper-review.md` severity
guidance.

## Cosim DMA / IOMMU map/unmap pairing  [CRITICAL]
[Any new mapping in src/lib/eftest/cosim.c (1599 lines) must have a
matching unmap on every error path. Address allocations have lifetime
tied to function scope. Past test instability has been traced to
unmatched map/unmap.]
- correct pattern: scope-bounded mapping with cleanup goto
- Bug pattern: mapping leaks on error path

## EVQ phase-bit and sentinel checks must be split  [HIGH]
[When modifying event-queue polling in src/lib/eftest/ef10/event.c
(45KB) or src/lib/eftest/ef100/event.c (18KB), the sentinel check and
the phase-bit check are separate operations. Conflating them was the
SNAP-11426 series bug class. Don't introduce new phase-bit handling
without referencing the established split.]
- correct pattern: separate sentinel and phase-bit checks
- Bug pattern: conflated check that miscounts wraparound events

## Descriptor ring wraparound uses modulo against ring size  [CRITICAL]
[Producer/consumer index updates in ef10_vi.c (55KB) and ef100_vi.c
(46KB) wrap with `% ring_size`. Flag bare `idx++` without wrap or any
`idx + n` comparison without modulo. Off-by-one or missing-wrap is a
silent ring-overrun bug.]
- correct pattern: idx = (idx + 1) % ring_size
- Bug pattern: bare idx++ then comparison against ring_size

## Byte-order on descriptor encoding  [HIGH]
[Descriptor field stores go through cpu_to_le*/le*_to_cpu. Raw stores
into descriptor structs on endianness boundaries silently corrupt
fields on big-endian hosts.]
- correct pattern: descriptor->len = cpu_to_le32(value)
- Bug pattern: descriptor->len = value

## Post-FLR resource reinit sequence  [CRITICAL]
[Post-FLR sequence must be: eftest_func_flr_and_wait_completion ->
eftest_func_reinit_after_flr -> release+reallocate the SL controller
and qpair built on top. Applies anywhere FLR happens, not just in
tests. Stale resource use after FLR is a CRITICAL bug class.]
- correct + Bug code from draft-rules.md "Reinit Dependent Resources
After FLR / Function Reset"
```

For the rules without a draft-rules.md predecessor (cosim, EVQ, ring, byte-order), invent illustrative C examples that match the snapper coding style (`/* */` comments, `if( cond )` spacing per the codebase). Keep examples short — 4–8 lines each. The point is recognition, not exhaustive coverage.

- [ ] **Step 2: Verify**

Run: `grep -c "^## " guides/snapper/subsystem/hardware-invariants.md`
Expected: 5.

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/subsystem/hardware-invariants.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add guides/snapper/subsystem/hardware-invariants.md
git commit -m "docs(snapper): add subsystem/hardware-invariants.md"
```

---

## Task 8: Write `subsystem/tests.md`

**Files:**
- Modify (full rewrite from TODO stub): `guides/snapper/subsystem/tests.md`

**Source material:**
- Spec section "`tests.md` — test framework"
- Provenance: draft-rules.md rules "Code Duplication Across `EFTEST_IMPL` Bodies", "One Variant Per Function, `spec_add_variant_*` Naming", "Abort On Unexpected State Rather Than Continue", "Callback-Based Validation: Prefer Open / Process / Close", "Reinit Dependent Resources After FLR / Function Reset", "Reuse Existing Helpers Instead Of Re-Implementing"

Note: the FLR rule lives in two places — `hardware-invariants.md` (covers FLR anywhere) and `tests.md` (covers FLR in test code with controller/qpair reinit specifics). Cross-reference in both.

- [ ] **Step 1: Write the file**

```markdown
# Tests Subsystem Guide

Rules for code under `src/tests/`. Load when the diff touches
`src/tests/`, `EFTEST_IMPL`, `spec_add_variant`, or `eftest_func_flr*`.

## EFTEST_IMPL duplication: extract a shared `*_impl` helper
[A test body that is a near-copy of a sibling test in the same file
must lift the shared steps into a static helper. Sibling tests
delegate, passing only the differing variant.]
- correct + Bug code from draft-rules.md "Code Duplication Across
EFTEST_IMPL Bodies"

## `spec_add_variant_<kind>` naming, one variant per function
[Each variant added by its own clearly-named function. Coupled
booleans (AN-on/off + parallel-detect only valid when AN=on) collapse
into one enum, not two boolean variants.]
- correct + Bug code from draft-rules.md "One Variant Per Function..."

## Abort on unexpected state rather than continue
[When a helper returns an unexpected enum value or read fails on a
critical step (IDE/SPDM/MCDI), abort the test via eftest_verdict_fail
or propagate the error up immediately. Continuing past read failures
hides the real failure.]
- correct + Bug code from draft-rules.md "Abort On Unexpected State..."

## Open/process/close over nested callbacks
[Validation flows implemented as nested callbacks (callback calling
callback calling callback) read top-to-bottom poorly. Replace with
explicit open_X / validate / close_X triples.]
- correct code from draft-rules.md "Callback-Based Validation..."

## FLR sequence in tests: reinit controllers and qpairs after reset
[Post-FLR in test code: eftest_func_flr_and_wait_completion ->
eftest_func_reinit_after_flr -> cross_domain_ctrlr_reinit ->
cross_domain_qp_reinit. Reusing a stale qpair after FLR is a
CRITICAL bug. See `hardware-invariants.md` for the broader FLR rule.]
- correct + Bug code from draft-rules.md "Reinit Dependent Resources
After FLR"

## Reuse existing helpers; don't re-implement
[Before adding a fill_invalid_tx_optdesc-style helper, grep for an
existing one. Common offenders: TX-descriptor builders, MAC stat
collectors, packet counters, port-handle accessors.]
- correct + Bug code from draft-rules.md "Reuse Existing Helpers..."
```

- [ ] **Step 2: Verify**

Run: `grep -c "^## " guides/snapper/subsystem/tests.md`
Expected: 6.

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/subsystem/tests.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add guides/snapper/subsystem/tests.md
git commit -m "docs(snapper): write subsystem/tests.md"
```

---

## Task 9: Write `subsystem/scripts.md`

**Files:**
- Create: `guides/snapper/subsystem/scripts.md`

**Source material:**
- Spec section "`scripts.md` — Python + shell"
- Provenance: draft-rules.md rules "Avoid Bare `except:` In Python Scripts", "Python Hex Formatting: `{value:#x}` Over `0x{value:x}`", "Python f-strings Over `%` / `.format()` Mixing", "Shell Variable Expansion And `shellcheck`-Visible Bugs", "`argparse` Choices With Enum"

- [ ] **Step 1: Write the file**

```markdown
# Scripts Subsystem Guide

Rules for code under `scripts/` and `src/tools/cosim/`, covering both
Python (`.py`) and shell (`.sh`) sources. Load when the diff touches
any of these directories or file extensions.

## Python: no bare `except:`
[Catch the specific exception (SyntaxError, OSError, etc.) and include
its text in the log line. Bare except: swallows KeyboardInterrupt and
hides real failures.]
- correct + Bug code from draft-rules.md "Avoid Bare `except:`..."

## Python: `{value:#x}` over `0x{value:x}` for hex formatting
[Let the format spec produce the 0x prefix. The manual prefix is one
more thing to mistype.]
- correct + Bug code from draft-rules.md "Python Hex Formatting..."

## Python: stay consistent with f-strings vs % vs .format()
[If the file uses f-strings, don't introduce '%s/%s' % (...) or
'{}/{}'.format(...). Consistency within a file matters more than the
chosen style.]
- correct code from draft-rules.md "Python f-strings Over..."

## Python: `argparse` flags with fixed sets use `choices=list(EnumClass)`
[choices=list(EnumClass) with type=EnumClass rejects bad input at
parse time. No hand-rolled `if chip == 'hunt': ... elif ...`
validation alongside argparse.]
- correct code from draft-rules.md "`argparse` Choices With Enum"

## Shell: shellcheck zero-warning, quote spaces, `local` for fn vars
[shellcheck must pass with zero warnings (or a disable comment with
explanation). Quote paths with spaces. Use local on function-internal
variables. Watch for stray } in ${...} substitutions and extra
trailing braces.]
- correct + Bug code from draft-rules.md "Shell Variable Expansion..."
```

- [ ] **Step 2: Verify**

Run: `grep -c "^## " guides/snapper/subsystem/scripts.md`
Expected: 5.

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/subsystem/scripts.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add guides/snapper/subsystem/scripts.md
git commit -m "docs(snapper): add subsystem/scripts.md"
```

---

## Task 10: Rewrite `subsystem/subsystem.md` (trigger table)

**Files:**
- Modify (full rewrite from 2-row stub): `guides/snapper/subsystem/subsystem.md`

- [ ] **Step 1: Write the file**

```markdown
# Subsystem Guide Index

Load subsystem guides based on what the diff touches. Each row's
**Triggers** column lists directory prefixes and/or symbol patterns;
when any trigger matches the diff, the corresponding guide loads.

## Subsystem guides

| Subsystem | Triggers | File |
|-----------|----------|------|
| lib APIs            | src/lib/eftest/, src/include/ci/eftest/, eftest_func_, eftest_efvi_, sl_nvme_qpair_ | lib.md |
| MCDI                | mcdi_rpc, ef_mcdi_, eftest_mcdi_, MC_CMD_, MCDI_PAYLOAD | mcdi.md |
| Chip dispatch       | WITH_X4, WITH_EF10, WITH_EF100, WITH_EF_VI_, eftest_func_kind, eftest_func_stage, ef_vi_get_kind | chip-dispatch.md |
| Hardware invariants | src/lib/eftest/cosim.c, src/lib/eftest/ef10/, src/lib/eftest/ef100/, src/lib/eftest/ef_vi/, event.c, descriptor ring symbols, eftest_func_flr | hardware-invariants.md |
| Tests               | src/tests/, EFTEST_IMPL, spec_add_variant, eftest_func_flr | tests.md |
| Scripts             | scripts/, src/tools/cosim/, *.py, *.sh | scripts.md |

## Notes

- Multiple subsystems can match the same diff. All matching guides
  load; rules are additive.
- Triggers are matched against the diff's changed file paths and
  changed symbol names. Symbol triggers can be substrings (e.g.
  `eftest_func_` matches `eftest_func_get_pf`).
- The cross-cutting `technical-patterns.md` and `false-positive-guide.md`
  always load — they are not in this table.
```

- [ ] **Step 2: Verify the trigger table parses**

Run from the repo root:

```bash
uv run python -c "
from pathlib import Path
from bb_review.guidelines import parse_subsystem_triggers
triggers = parse_subsystem_triggers(Path('guides/snapper/subsystem/subsystem.md'))
for t in triggers:
    print(t['subsystem'], '->', t['file'])
"
```

Expected: prints 6 rows, one per subsystem, matching the table above (lib APIs -> lib.md, MCDI -> mcdi.md, Chip dispatch -> chip-dispatch.md, Hardware invariants -> hardware-invariants.md, Tests -> tests.md, Scripts -> scripts.md). If any row is missing or the file column is wrong, the markdown table is malformed — fix and re-run.

- [ ] **Step 3: Commit**

```bash
git add guides/snapper/subsystem/subsystem.md
git commit -m "docs(snapper): rewrite subsystem/subsystem.md trigger table"
```

---

## Task 11: Expand `skills/snapper.md`

**Files:**
- Modify: `guides/snapper/skills/snapper.md` (currently 55 lines, will be ~120 lines)

**Source material:**
- Spec section "`skills/snapper.md` expansion"
- Existing file content (preserve frontmatter, "Project Overview" intro, and the false-positive notes — they're still relevant)
- README-Devel.md for the line-numbered authority references

- [ ] **Step 1: Read the current file**

Run: `cat guides/snapper/skills/snapper.md`
Confirms: the existing file has frontmatter (`name: snapper`, `description: ...`), a Project Overview section, Activation Criteria, Key Conventions (4 false-positive notes), and Capabilities. Keep the frontmatter and the structure; expand the content.

- [ ] **Step 2: Write the expanded file**

Final shape:

```markdown
---
name: snapper
description: Load when reviewing Snapper (AMD NIC validation tool) code. Mature C codebase with lib/ APIs, eftest framework, and 10+ years of conventions.
---

## ALWAYS READ

1. `{{GUIDE_DIR}}/technical-patterns.md` — cross-cutting review rules
2. `{{GUIDE_DIR}}/false-positive-guide.md` — patterns NOT to flag

These two files are MANDATORY for every Snapper review.

## Project Overview

Snapper is a software system used by AMD to validate NICs. It has been
used for more than 10 years for both pre-silicon and post-silicon
testing of EF10, EF100, and X4 generation NICs. The repository's
`README-Devel.md` is the source of truth for naming, comment style,
Doxygen, and verdict conventions — consult it when a rule's authority
is unclear (it is line-cited in `technical-patterns.md`).

## Codebase Tour

| Area | Path | Notes |
|------|------|-------|
| Core test framework  | `src/lib/eftest/`             | ~80% of lib code; per-chip implementations in `ef10/` and `ef100/` |
| Protocol packets     | `src/lib/cinet/`              | eth, ip, tcp, udp, vxlan, gre, ipsec, geneve helpers |
| QEMU/RTL bridge      | `src/lib/eftest/cosim.c`      | 1599 lines; DMA / IOMMU mapping; high-risk |
| Public headers       | `src/include/ci/eftest/`      | The public lib API; internal struct bodies stay out |
| Tests                | `src/tests/nic/eftests/`      | 100+ individual tests under EFTEST_IMPL |
| Scripts              | `scripts/`, `src/tools/cosim/`| Python + shell |

## Build and tooling

- Primary build: Meson (`meson.build`). `mk/` mmake is legacy.
- `-Werror` is enforced — warnings fail the build (`warn_error` in
  `meson_options.txt`).
- Code formatting: `clang-format` 11+ at `src/.clang-format`.
- Shell linting: `shellcheck` zero-warning policy.
- Reviewboard is required for commits (README-Devel.md line 80).

## Legacy-code pragma

Roughly 75% of the codebase predates the current conventions
(README-Devel.md line 84). The `technical-patterns.md` rules apply to
**new files and modified lines only**. Do not propose rewrites of
legacy style the patch did not touch. See `false-positive-guide.md`
for the precise scoping.

## License header

Copyright line says `AMD, Inc., All rights reserved.` — not Xilinx.
Current year only; no duplicate-year ranges (e.g. `2024 - 2024`).
Boilerplate disclaimer preserved verbatim.

## Activation Criteria

Engage when these markers appear in the repository:
- Snapper-specific directory structure (`src/lib/eftest/`, `src/tests/nic/eftests/`)
- Files using `eftest_`, `sl_`, `ef_`, or `ci_` prefixed APIs
- `meson.build` at repo root with snapper module imports

## Capabilities

### Patch Review

When asked to review a patch:
1. Read `{{GUIDE_DIR}}/technical-patterns.md`
2. Read `{{GUIDE_DIR}}/false-positive-guide.md`
3. Follow the protocol in `{{REVIEW_GUIDE}}`
4. Match the diff against triggers in `{{GUIDE_DIR}}/subsystem/subsystem.md` and load matching subsystem guides

### Subsystem Context

When working on code in specific areas, read
`{{GUIDE_DIR}}/subsystem/subsystem.md` and load matching subsystem
guides.
```

- [ ] **Step 3: Placeholder scan and frontmatter sanity**

Run: `head -5 guides/snapper/skills/snapper.md`
Expected: shows frontmatter with `name: snapper` and a `description:` line.

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/skills/snapper.md`
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add guides/snapper/skills/snapper.md
git commit -m "docs(snapper): expand skills/snapper.md with codebase tour and pragmas"
```

---

## Task 12: Expand `slash-commands/snapper-review.md`

**Files:**
- Modify: `guides/snapper/slash-commands/snapper-review.md` (currently 50 lines, will be ~100 lines)

**Source material:**
- Spec section "`slash-commands/snapper-review.md` expansion"

- [ ] **Step 1: Write the expanded file**

```markdown
# Snapper Patch Review Protocol

Review patches to the Snapper NIC validation tool.

## Setup (read in this order)

1. `technical-patterns.md` — cross-cutting rules (sections A through I)
2. `false-positive-guide.md` — patterns NOT to flag
3. `subsystem/subsystem.md` — trigger table
4. Match the diff against triggers; load each matching subsystem guide
5. Consult `README-Devel.md` in the repo when a rule needs deeper authority — it is line-cited in `technical-patterns.md`

## Legacy-code reminder

Roughly 75% of the codebase predates current conventions
(README-Devel.md line 84). Enforce rules on **new files and modified
lines only**. Do not propose rewrites of legacy style the patch did
not touch. The `false-positive-guide.md` covers the precise edges.

## Review protocol (staged)

1. **File-scope check** (per file in the diff):
   - Copyright header: AMD Inc., current year, no Xilinx, no duplicate-year ranges
   - License/boilerplate preserved verbatim
   - File-header Doxygen (`/** @file @brief ... */`) for any new file with Doxygen comments
   - Include order alphabetical within each group; new includes match the existing block's order

2. **Cross-cutting rules** — apply `technical-patterns.md` sections A–I to new/modified lines:
   - A: Naming and identifiers (prefix discipline, function-name scope, spelling, AMD-not-Xilinx, helper reuse)
   - B: Comments and documentation (`/* */` only, Doxygen detail)
   - C: File-level conventions (copyright, includes, `static` on local helpers)
   - D: Control flow (NULL comparisons, brace symmetry, `default:`-only switches, `while (1)` → `for`, loop bounds, blank line after declarations, `#if` clusters)
   - E: Constants and types (magic numbers, format specifiers, bool returns, enums over bool, `const char*` for `*_to_str`, named error codes)
   - F: Memory (alloc/free pairing, `mmap` checks `MAP_FAILED`)
   - G: API design (out-param naming, optional out-param NULL check)
   - H: Logging/verdicts (no `\n`, no leading whitespace, no `__func__`, verdict naming, iteration index, no re-log)
   - I: Commit/review (one logical change, description quality, FIXME tickets, no drive-by reformatting)

3. **Subsystem rules** — apply each loaded subsystem guide to its triggered code:
   - `lib.md` — lib API design
   - `mcdi.md` — MCDI RPC wrappers
   - `chip-dispatch.md` — per-chip / per-kind dispatch
   - `hardware-invariants.md` — cosim, EVQ, descriptor rings, byte-order, FLR
   - `tests.md` — EFTEST_IMPL, spec_add_variant, FLR-in-tests, open/process/close
   - `scripts.md` — Python and shell

4. **Architecture / patch scope**:
   - One logical change per commit
   - Commit message explains *why*
   - No unrelated reformatting inside a feature patch
   - `FIXME` macros reference a real, correctly-spelled ticket

## Severity guidance

| Severity | Examples |
|----------|----------|
| CRITICAL | Memory unsafety, missing free on error path, descriptor-ring wraparound off-by-one, stale resource use after FLR, unmatched cosim DMA mapping |
| HIGH     | HW-touching correctness (cosim mapping, EVQ phase-bit, byte-order), missing `CI_BUILD_ASSERT` on MCDI enum pairs, hand-rolled MCDI lengths, missing `CI_MIN` clamp on payload copy |
| MEDIUM   | API design (enum-over-bool on new exported APIs, missing `static`, missing Doxygen on new exported functions), magic numbers, NULL-comparison style |
| LOW      | Spelling, log-format niceties (no `__func__`, no `\n`, no leading whitespace), brace symmetry, blank-line discipline |

## Output format

Report issues as `### Issue:` blocks with:
- `file:line` reference
- Severity (CRITICAL / HIGH / MEDIUM / LOW)
- The rule name from `technical-patterns.md` or the matching subsystem guide
- A code snippet of the bug
- A code snippet of the fix

**No emojis** in review text — Review Board returns 500 on emoji content.

## Archival reference

`draft-rules.md` is the original flat draft from which these rules
were derived. It is **not loaded** by the protocol — it exists only
for provenance. If a rule there disagrees with `technical-patterns.md`
or a subsystem guide, the structured guides win.
```

- [ ] **Step 2: Placeholder scan**

Run: `grep -nE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/slash-commands/snapper-review.md`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add guides/snapper/slash-commands/snapper-review.md
git commit -m "docs(snapper): expand slash-commands/snapper-review.md with staged protocol and severity table"
```

---

## Task 13: Smoke-test verification — `load_rich_context()` picks up new files

**Files:**
- No code changes; verification only.

This task confirms that `bb_review.guidelines.load_rich_context()` correctly assembles the new guides for a sample diff. It exercises three code paths:
- Always-loaded files (`technical-patterns.md`, `false-positive-guide.md`)
- Trigger table parsing (`subsystem/subsystem.md`)
- Subsystem matching against changed files

- [ ] **Step 1: Verify always-loaded files appear in the context**

Run:

```bash
uv run python -c "
from bb_review.guidelines import load_rich_context
ctx = load_rich_context('snapper', changed_files=[])
print('technical-patterns present:', 'Snapper Review Patterns' in ctx)
print('false-positive present:', 'False-Positive' in ctx)
print('context length:', len(ctx))
"
```

Expected: both flags `True`, context length > 10000 characters. If `False`, the file titles don't match what the loader concatenates — check that `technical-patterns.md` opens with `# Snapper Review Patterns` (Task 2) and `false-positive-guide.md` opens with a heading containing `False-Positive` (Task 3).

- [ ] **Step 2: Verify lib subsystem triggers on lib/ file paths**

Run:

```bash
uv run python -c "
from bb_review.guidelines import load_rich_context
ctx = load_rich_context('snapper', changed_files=['src/lib/eftest/foo.c'])
print('lib.md loaded:', 'lib/ Subsystem Guide' in ctx)
print('mcdi.md NOT loaded:', 'MCDI Subsystem Guide' not in ctx)
"
```

Expected: `lib.md loaded: True`, `mcdi.md NOT loaded: True` (the second is a sanity check that unrelated subsystems don't load on non-matching paths).

- [ ] **Step 3: Verify multi-subsystem matching**

Run:

```bash
uv run python -c "
from bb_review.guidelines import load_rich_context
ctx = load_rich_context('snapper', changed_files=[
    'src/lib/eftest/mcdi_telemetry.c',
    'src/tests/nic/eftests/foo.c',
    'scripts/build/helper.py',
])
for marker, label in [
    ('lib/ Subsystem Guide',       'lib.md'),
    ('MCDI Subsystem Guide',       'mcdi.md'),
    ('Tests Subsystem Guide',      'tests.md'),
    ('Scripts Subsystem Guide',    'scripts.md'),
]:
    print(f'{label}:', marker in ctx)
"
```

Expected: all four `True`. If a subsystem fails to load, check its trigger row in `subsystem/subsystem.md` against the changed-file path or symbol pattern.

- [ ] **Step 4: Verify hardware-invariants triggers on cosim.c**

Run:

```bash
uv run python -c "
from bb_review.guidelines import load_rich_context
ctx = load_rich_context('snapper', changed_files=['src/lib/eftest/cosim.c'])
print('hardware-invariants loaded:', 'Hardware-Invariants' in ctx)
print('lib.md also loaded:', 'lib/ Subsystem Guide' in ctx)
"
```

Expected: both `True` — `cosim.c` matches both lib/ APIs (via `src/lib/eftest/` prefix) and hardware invariants (via the explicit `src/lib/eftest/cosim.c` trigger). Multi-match is the designed behaviour.

- [ ] **Step 5: Verify chip-dispatch triggers on symbol**

Run:

```bash
uv run python -c "
from bb_review.guidelines import load_rich_context
ctx = load_rich_context('snapper', changed_files=['src/lib/eftest/dispatch_helper.c'])
# symbol-level matching is done against changed-file content in reality;
# this checks the path-level part of the trigger table is well-formed
print('chip-dispatch markup parseable:', True)  # parse_subsystem_triggers
                                                  # already ran above
from pathlib import Path
from bb_review.guidelines import parse_subsystem_triggers
triggers = parse_subsystem_triggers(Path('guides/snapper/subsystem/subsystem.md'))
chip = [t for t in triggers if t['file'] == 'chip-dispatch.md']
print('chip-dispatch row:', chip[0] if chip else 'MISSING')
"
```

Expected: the chip-dispatch row prints with all its trigger symbols intact. If the row is `MISSING`, the markdown table has a malformed row — fix and re-run.

- [ ] **Step 6: No commit required**

This task is verification only. If any check failed, return to the relevant earlier task and fix the file. If all checks passed, proceed to the wrap-up below.

---

## Wrap-up

After all 13 tasks complete and the smoke-test verification passes:

- [ ] **Run a final placeholder sweep across the whole guides/snapper/ tree:**

```bash
grep -rnE "TBD|TODO|fill in|XXX|placeholder" guides/snapper/ | grep -v draft-rules.md
```
Expected: no output (matches inside `draft-rules.md` are fine — that file is archival and may contain TODOs as part of the historical record).

- [ ] **Confirm the file inventory:**

```bash
find guides/snapper -type f | sort
```
Expected output:
```
guides/snapper/draft-rules.md
guides/snapper/false-positive-guide.md
guides/snapper/skills/snapper.md
guides/snapper/slash-commands/snapper-review.md
guides/snapper/subsystem/chip-dispatch.md
guides/snapper/subsystem/hardware-invariants.md
guides/snapper/subsystem/lib.md
guides/snapper/subsystem/mcdi.md
guides/snapper/subsystem/scripts.md
guides/snapper/subsystem/subsystem.md
guides/snapper/subsystem/tests.md
guides/snapper/technical-patterns.md
```

- [ ] **Confirm smartnic-snapper is gone:**

```bash
test ! -d guides/smartnic-snapper && echo "OK: smartnic-snapper removed" || echo "FAIL: smartnic-snapper still exists"
```
Expected: `OK: smartnic-snapper removed`.

If anything is off, fix in-place — these are not commit-worthy issues by themselves; they should have been caught by Task 1 or Task 13.
