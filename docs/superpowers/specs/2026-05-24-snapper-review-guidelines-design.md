# Snapper Review Guidelines — Design

**Date:** 2026-05-24
**Target:** `guides/snapper/` (canonical; `guides/smartnic-snapper/` is removed)
**Sources:** `/home/kostik/repos/amd-smartnic-snapper/README-Devel.md`, `guides/snapper/draft-rules.md`, deep codebase analysis

## Goal

Replace the current stub-and-draft state of `guides/snapper/` with a comprehensive set of review instructions that the bb-review system can load (both direct-LLM and agent backends) and apply to Snapper patches. Three sources contribute:

1. `draft-rules.md` — ~60 flat rules already drafted from real-world review findings
2. `README-Devel.md` — ~30 explicit MUSTs (Doxygen detail, file headers, naming-scope, verdict 32-char limit, etc.) not in the draft
3. New subsystem invariants surfaced by codebase analysis (cosim DMA/IOMMU, EVQ phase-bits, descriptor ring wraparound, FLR reinit sequence)

## Non-goals

- Not designing review automation for any other repo.
- Not changing bb-review's loading machinery (`guidelines.py`, `guidelines_deploy.py`).
- Not rewriting `draft-rules.md` content for content's sake — its examples are valuable; we reorganize into per-file homes and add what's missing.

## Decisions made up-front

| Decision | Choice |
|---|---|
| Directory | Use `guides/snapper/`. Delete `guides/smartnic-snapper/`. |
| Scope | Draft rules + README-Devel MUSTs + new subsystem invariants (largest scope). |
| Organization | Subsystem-sharded. Cross-cutting rules in `technical-patterns.md`; subsystem-scoped rules in `subsystem/*.md`. |
| `draft-rules.md` | Kept in-place, marked archival/superseded in its file header. Not loaded by the review protocol. |

## Final structure

```
guides/snapper/
  skills/snapper.md                    (EXPANDED ~120 lines)
  slash-commands/snapper-review.md     (EXPANDED ~100 lines)
  technical-patterns.md                (REWRITTEN: cross-cutting, 9 sections, ~43 rules)
  false-positive-guide.md              (NEW)
  subsystem/
    subsystem.md                       (trigger table, expanded)
    lib.md                             (REWRITTEN from stub)
    mcdi.md                            (NEW)
    chip-dispatch.md                   (NEW)
    hardware-invariants.md             (NEW — deep HW-touching correctness)
    tests.md                           (REWRITTEN from stub)
    scripts.md                         (NEW)
  draft-rules.md                       (KEPT, marked archival in header)

guides/smartnic-snapper/               (DELETED — duplicate)
```

## `technical-patterns.md` — section-by-section content

A cross-cutting pragma stated at the top of the file: legacy code (~75% of the codebase per README-Devel.md line 84) predates current conventions. Enforce on new files and modified lines only; don't reject patches for legacy style they didn't touch.

### Section A — Naming and identifiers (~5 rules)

- Symbol-prefix discipline: `ef_` = production HW API, `eftest_` = test/FW-specific helpers, `sl_` = snapper-level objects, `ci_` = non-HW helpers. Library headers use the same prefix as the public header they back.
- Function-name scope: `__prefix_foo` static, `prefix_foo` lib-local, `eftest_prefix_foo` global. Static must not be unprefixed; static must not be `eftest_*`.
- Spelling in identifiers/strings/comments/commit messages. Known typos from draft-rules: `enrty`, `Sowtware`, `Accociated`, `reqeust`, `repsponsible`, `resreved1`, `APPLYIED`, `CONTEROLLER`, `Tmeout`, `autor`, `enw`, `alrets`. Also: `Xilinx` in new files.
- AMD-not-Xilinx in copyright and identifiers in new files.
- Reuse existing helpers; don't re-implement under a different name (TX-descriptor builders, MAC stat collectors, packet counters, port-handle accessors are common offenders).

### Section B — Comments and documentation (~7 rules)

- C-style `/* */` only; no `//`.
- Multi-line comment alignment: opening `/*` alone on first line; `*` aligned on continuation lines; text starts on line 2+.
- Doxygen `/** ... */` for every exported function, struct field (in public structs), and enum value.
- `@param[out]` for output parameters, `@param` for input-only; `@return` when non-void.
- Doxygen parameters in declaration order; no blank lines within parameter list; blank line between parameters and result.
- File headers `/** @file @brief ... */` for any file with Doxygen comments — placed before any code or `#ifdef`.
- No `@brief` keyword (brief is auto-derived from the first sentence). Don't comment forward-declared typedefs; document the actual struct.

### Section C — File-level conventions (~3 rules)

- License/copyright header: `AMD, Inc., All rights reserved.`, current year, no `Xilinx`, no duplicate-year ranges (`2024 - 2024`). Preserve disclaimer verbatim.
- Header include order: alphabetical within each group; eftest/ci groups separate. New includes added to existing files must respect the existing block's order.
- Local helpers must be `static`; no `extern` on local prototypes inside `.c`. (In headers, `extern` is conventional and acceptable.)

### Section D — Control flow and structure (~7 rules)

- NULL pointer comparisons explicit: `== NULL` / `!= NULL`. No `if (!ptr)` / `if (ptr)` on pointer types.
- If/else brace symmetry: both branches use `{ ... }` or neither does.
- Pointless `default:`-only switches: delete the switch or add real cases. Exception: forward-compat scaffold for an enum with values yet to be added; require a comment explaining.
- `while (1)` + decrement-and-break for known-bounded iteration → `for` loop with explicit counter.
- Loop bound uses actually-filled count, not `<= last`. Using `<= last` over-reads when a helper increments past the populated tail.
- Blank line after the variable-declaration block at the top of a function body. No extra blank line immediately after `{`.
- Excessive `#if`/`#endif` clusters in function bodies: hoist into a helper, a single `bool fail = false;` block guarded once, or a `UNUSED(x)` shim. When the cluster grows large, restructure so guards appear once at top/bottom of the block, not interleaved per-statement.

### Section E — Constants and types (~6 rules)

- Magic numbers: literal integers used as size/limit/count/default/array-bound (other than `0`, `1`, or small explicit patterns) must be `#define`d with an intent-bearing name. Cite the spec section in a comment when introducing a spec-derived define.
- Format specifiers for typedef'd integers: use `PRIu32`/`PRIx32`/etc. for typedefs like `eftest_mcdi_telemetry_source_id_t`. Or define a `_FMT` macro alongside the type.
- Boolean-returning functions return `bool`, not `int` 0/1.
- Enums over `bool` for direction/role/mode arguments in public APIs (`SL_NVME_DIR_DEVICE_TO_HOST` over `true`).
- `*_to_str` / `*_str` helpers that only return string literals must return `const char*`.
- Error-code literals named: `-EF_MCDI_ERR_ERANGE`, not `-34`. Applies to log/verdict messages.

### Section F — Memory and resources (~2 rules)

- `eftest_malloc()` paired with `eftest_free()` (or equivalent) on every exit path, including error paths.
- `mmap()` return compared to `MAP_FAILED`, not `NULL`.

### Section G — API design (cross-cutting) (~2 rules)

- Out-parameter naming: `data_size_in` (caller-supplied buffer capacity) vs `data_size_out` / `out_size` (actual written length). Names must distinguish input from output.
- Optional out-params: check `out_size != NULL` before dereferencing — these are optional outputs.

### Section H — Logging, steps, verdicts (~7 rules)

- No `\n` in `step`/`substep`/`log`/`vlog`/`zlog`/`eftest_verdict*` format strings. The framework adds its own newline.
- No leading whitespace in log/step format strings. The framework adds its own prefix.
- No `__func__` in log format. The log subsystem already tags the source. If useful for grep, hardcode the tag as a string literal.
- Verdict names: `Subsystem-Object-Condition-Detail`, Title-Case, hyphen-separated, max 32 chars. Each name unique across the codebase (or add an index/differentiator).
- Verdict messages: verb + actual variable values, formatted with `printf`-style specifiers. Example: `eftest_verdict_fail_verb("SYS-Mismatch", "SYS Clocks mismatch - read=%" PRId32 ", expected=%" PRId32, sys_clock, sys_clk);`.
- Iteration index in `step`/`substep` and verdict format strings inside loops. Each iteration must produce a distinct, searchable line.
- Don't re-log errors that callees already logged via `eftest_verdict_*` / `log()`. If the callee's log is missing context, fix the callee instead.

### Section I — Commit / review process (~4 rules)

- One logical change per commit. Don't mix renames + behaviour changes or struct restructures + new fields. Commit messages must match the diff (no quiet header reordering inside a "X4-6594: reinit vport" commit).
- Commit description quality: explain *why*. Bug fixes briefly describe the bug (1–2 sentences). Trivial mechanical changes still need a one-line rationale.
- `FIXME` / workaround macros must reference a real, correctly-spelled ticket (`SNAP-11338`, not `SNAP_11388`). Chip-specific FIXMEs need a tag and reference to the commit that introduced them.
- No unrelated whitespace / clang-format-only edits inside a feature patch. Exception: a brand-new file is allowed to be fully formatted as part of its introducing commit.

## Subsystem files

### `lib.md` — lib/ API design (~6 rules)

- Internal-vs-public header boundary: don't move types from `src/lib/eftest/*` (internal) into `src/include/ci/eftest/*` (public) without justification. Internal struct bodies (`rxgen_ctx_t`, `rxgen_pkt_t`, `rxgen_rcpt_t`) stay private. Tests should not depend on lib internals.
- Prefer accessor helpers over raw field access: `eftest_func_get_pf()`, `eftest_func_user()`, `eftest_func_bdf()`, `eftest_func_kind()`, `eftest_efvi_func()`. Don't inline `func->user`/`func->bdf` etc.
- Pseudo-header API parity: `eftest_efvi_rx_pseudo_hdr_size(...)` and `eftest_efvi_tx_pseudo_hdr_size(...)` keep parameter shape in sync. A patch updating one must update the other (or document why not).
- Common inner body for wait/process completion pairs: extract `sl_nvme_qpair_process_completion(qp, &cmpl)` and have both `sl_nvme_qpair_wait_completion` and `sl_nvme_qpair_get_completion` call it.
- Per-VI / per-kind value lookup lives in lib helpers, not in test code. Style of the helper is covered in `chip-dispatch.md`; the lib.md rule is about *where* it lives.
- Per-kind dispatch in stats files (`src/lib/eftest/stats/port_alerts.c` pattern) goes inside the dispatcher (`switch (eftest_func_kind(func))`), not scattered `#if WITH_X4` blocks across the file.

### `mcdi.md` — MCDI RPC wrappers (~4 rules)

- Use `MC_CMD_..._OUT_LEN` / `..._OUT_LENMIN` / `..._OUT_LENMAX` macros, never hand-written byte sizes (e.g. `mcdi_rpc(func, &req, 16, NULL)` is wrong).
- `CI_BUILD_ASSERT` every member when a snapper-level enum (`eftest_*_t`) mirrors an MCDI enum (`MC_CMD_..._OUT_*` or `TELEMETRY_EVENT_..._TYPE_*`). Missing asserts are a real bug source.
- `CI_MIN`-clamp payload copies into a caller-provided buffer against caller-supplied capacity. Don't trust server-provided length: `memcpy(dst, src, CI_MIN(payload_size, data_size_in))`.
- Blank line between the `mcdi_rpc()` reply-length argument and the field-extraction block.

### `chip-dispatch.md` — per-chip / per-kind dispatch (~4 rules)

- Long `switch (eftest_func_kind(func))` blocks hoist into a `query()` / `dispatch()` helper that returns the right `rc` / value; callers stay flat.
- Derived `WITH_FEATURE_CHIP` flags when a feature is gated by two `WITH_*` simultaneously (e.g. `WITH_PORT_ALERTS && WITH_X4` → `WITH_PORT_ALERTS_X4`). No nested `#if` at every use site.
- Runtime per-kind dispatch via `ef_vi_get_kind()` / `eftest_func_kind()` preferred over compile-time `#if WITH_EF_VI_X4_LL` selectors when both kinds can coexist in one build.
- Static helper `*_for_vi()` style for per-kind value lookup (e.g. `unsolicited_events_max_for_vi(vi)`).

### `hardware-invariants.md` — HW-touching correctness (~5 rules)

- **Cosim DMA / IOMMU**: any new mapping in `src/lib/eftest/cosim.c` must have a matching unmap on every error path. Address allocations have lifetime tied to function scope. Mapping leaks have been the root cause of past test instability — treat unmatched map/unmap as a CRITICAL finding.
- **EVQ phase-bit tracking**: when modifying event-queue polling in `src/lib/eftest/ef10/event.c` (45KB) or `src/lib/eftest/ef100/event.c` (18KB), sentinel and phase-bit checks must be split, per the SNAP-11426 series refactor. Don't conflate them; don't introduce new phase-bit handling without referencing the established pattern.
- **Descriptor ring wraparound**: producer/consumer index updates in `ef10_vi.c` (55KB) and `ef100_vi.c` (46KB) use modulo arithmetic against ring size. Flag bare `idx++` without wrap, or any `idx + n` comparison without `% ring_size`.
- **Byte-order on descriptor encoding**: descriptor field stores go through `cpu_to_le*` / `le*_to_cpu`. Flag raw stores into descriptor structs on endianness boundaries.
- **FLR + dependent-resource reinit**: post-FLR sequence must be `eftest_func_flr_and_wait_completion` → `eftest_func_reinit_after_flr` → release+reallocate the SL controller/qpair built on top. Applies anywhere FLR happens (not just in tests). Stale resource use after FLR is a CRITICAL bug class.

### `tests.md` — test framework (~6 rules)

- `EFTEST_IMPL(...)` body that is a near-copy of a sibling test in the same file → extract `*_impl(test_ctx, ...)` static helper; sibling tests delegate with their specific variants.
- `spec_add_variant_<kind>` naming. One variant per function. Collapse coupled booleans into one enum (e.g. AN-on/off with parallel-detect → one `autoneg_modes` enum with `off`/`on`/`parallel`).
- Abort on unexpected state in tests (`eftest_verdict_fail` or propagate error up) rather than continuing with bad data. Continuing past read failures hides the real failure.
- Open/process/close pattern over nested callbacks. Replace `callback(callback(callback(...)))` with `addr = open_*; validate(addr); process(addr); close_*(addr);` so the test reads top-to-bottom.
- FLR sequence in tests: `eftest_func_flr_and_wait_completion` → `eftest_func_reinit_after_flr` → `cross_domain_ctrlr_reinit` → `cross_domain_qp_reinit`. Don't reuse stale controllers/qpairs.
- Don't re-implement TX-descriptor builders, MAC stat collectors, packet counters, or port-handle accessors. Grep first; `fill_invalid_tx_optdesc` and friends already exist.

### `scripts.md` — Python + shell (~5 rules)

- Python: no bare `except:`. Catch the specific exception (`SyntaxError`, `OSError`, etc.) and include its text in the log line. Bare `except:` swallows `KeyboardInterrupt`.
- Python: `{value:#x}` over `0x{value:x}` for hex formatting — prefix lives in the format spec.
- Python: stay consistent within a file. If the file uses f-strings, don't introduce `'%s/%s' % (...)` or `'{}/{}'.format(...)`.
- Python: `argparse` flags with fixed sets use `choices=list(EnumClass)` with `type=EnumClass`. No hand-rolled `if chip == 'hunt': ... elif ...` validation alongside argparse.
- Shell: `shellcheck` zero-warning policy. Quote paths with spaces. `local` on function-internal variables. Watch for stray `}` in `${...}` substitutions and extra trailing braces.

### `subsystem/subsystem.md` — trigger table

```markdown
| Subsystem | Triggers | File |
|-----------|----------|------|
| lib APIs            | src/lib/eftest/, src/include/ci/eftest/, eftest_func_, eftest_efvi_, sl_nvme_qpair_      | lib.md |
| MCDI                | mcdi_rpc, ef_mcdi_, eftest_mcdi_, MC_CMD_, MCDI_PAYLOAD                                    | mcdi.md |
| Chip dispatch       | WITH_X4, WITH_EF10, WITH_EF100, WITH_EF_VI_, eftest_func_kind, eftest_func_stage, ef_vi_get_kind | chip-dispatch.md |
| Hardware invariants | src/lib/eftest/cosim.c, src/lib/eftest/ef10/, src/lib/eftest/ef100/, src/lib/eftest/ef_vi/, event.c, descriptor ring symbols, eftest_func_flr | hardware-invariants.md |
| Tests               | src/tests/, EFTEST_IMPL, spec_add_variant, eftest_func_flr                                 | tests.md |
| Scripts             | scripts/, src/tools/cosim/, *.py, *.sh                                                     | scripts.md |
```

## `false-positive-guide.md` content

Already-known false positives (from current `technical-patterns.md` and `skills/snapper.md`):

- **Indirect includes in `.c` files**: intentional via header chains. Don't demand direct includes.
- **Local/static functions don't validate arguments**: caller responsibility — codebase convention.
- **Code after `eftest_verdict_fail*` is unreachable**: these exit. Don't flag "missing error handling" or "dead code" after.
- **Missing assertions in non-debug build paths**: assertions are debug-only.

Adding from the deep analysis:

- **Legacy-code pragma**: ~75% of the codebase predates current conventions (README-Devel.md line 84). Don't flag legacy style violations on lines the patch didn't touch. Enforce on new files and modified lines only.
- **Existing FIXME-guarded workarounds**: don't flag a workaround that already has a `FIXME_*` macro and ticket reference. Only flag new workarounds missing a tag, or FIXME tags with typo'd ticket numbers.
- **Old `eftest_*` naming on legacy symbols**: don't push for renaming legacy `eftest_*` symbols to `ef_*` / `sl_*`. The naming migration is gradual per README-Devel.md line 88–127; only enforce modern prefix on new symbols.
- **Existing `#if WITH_X4` etc. on legacy code paths**: don't flag existing compile-time chip selectors that work today; only flag new test-side `#if WITH_*` blocks that should be runtime dispatch.

## `skills/snapper.md` expansion

Adds, on top of the current 55-line file:

- Brief codebase tour: `src/lib/eftest/` (core test framework), `src/lib/cinet/` (protocol packet construction), `src/lib/eftest/cosim.c` (QEMU/RTL bridge), `src/tests/nic/eftests/` (100+ tests), `scripts/` and `src/tools/cosim/` (Python + shell).
- Build basics: Meson (`meson.build`) is primary; `mk/` mmake is legacy. `-Werror` enforced — warnings fail the build.
- Automated checks reviewers rely on: `clang-format` (config at `src/.clang-format`, version 11+), `shellcheck` zero-warning policy.
- License header authority: AMD, Inc. (not Xilinx), current year.
- Reviewboard required for commits (README-Devel.md line 80).
- Mandatory reads expanded: `technical-patterns.md` AND `false-positive-guide.md` (both, every review).
- Legacy-code pragma stated up-front.

## `slash-commands/snapper-review.md` expansion

Replaces the current 50-line file with a ~100-line protocol:

**Setup (in order):**
1. Read `technical-patterns.md` (cross-cutting rules)
2. Read `false-positive-guide.md` (don't-flag list)
3. Read `subsystem/subsystem.md` (trigger table)
4. Match diff against triggers; load matching subsystem guides
5. Consult `README-Devel.md` in the repo if a rule needs deeper authority

**Review protocol (staged):**

1. **File-scope check**: copyright header / license / file-header Doxygen / include order
2. **Cross-cutting rules**: apply `technical-patterns.md` sections A–I to new/modified lines
3. **Subsystem rules**: apply each loaded subsystem guide to its triggered code
4. **Architecture / patch scope**: one logical change per commit, no unrelated reformatting, commit message quality

**Severity guidance:**

| Severity | Examples |
|---|---|
| CRITICAL | memory unsafety, missing free on error path, descriptor-ring wraparound off-by-one, stale resource use after FLR, unmatched cosim DMA mapping |
| HIGH | HW-touching correctness (cosim mapping, EVQ phase-bit, byte-order), missing `CI_BUILD_ASSERT` on MCDI enum pairs, hand-rolled MCDI lengths, missing `CI_MIN` clamp on payload copy |
| MEDIUM | API design (enum-over-bool on new exported APIs, missing `static`, missing Doxygen on new exported functions), magic numbers, NULL-comparison style |
| LOW | spelling, log-format niceties (no `__func__`, no `\n`, no leading whitespace), brace symmetry, blank-line discipline |

**Legacy-code reminder** at top of protocol: enforce only on new files and modified lines.

**Output format**: `### Issue:` blocks with `file:line`, severity, rule name from technical-patterns or subsystem guide, bug snippet, fix snippet. No emojis (RB returns 500).

**`draft-rules.md` reference**: marked archival in its file header — not loaded by the protocol, kept for historical reference and provenance of the current rules.

## Sequencing and risk

The work is rule-organization, not code changes. Risk is low. Suggested ordering for the implementation plan:

1. Write `technical-patterns.md` rewrite first — it's the largest single artifact and every other file references it.
2. Write the six subsystem files in parallel — each is small and independent.
3. Write `false-positive-guide.md` — independent.
4. Rewrite `subsystem/subsystem.md` trigger table.
5. Expand `skills/snapper.md` and `slash-commands/snapper-review.md`.
6. Add archival header to `draft-rules.md`.
7. Delete `guides/smartnic-snapper/`.

Each file is independently reviewable. A sanity check after each: confirm the trigger-table prefixes match what `guidelines.py` matches against, and confirm rule snippets stay close to the draft-rules wording where they originate (don't paraphrase away accuracy).

## Out of scope (deliberately)

- Editing `guidelines.py` or `guidelines_deploy.py`.
- Adding new bb-review CLI flags.
- Validating any of the rules against a live Snapper patch — that's a separate exercise.
- Encoding the legacy-vs-new naming gradient (legacy `eftest_*` symbols allowed; new symbols use modern prefixes) as a subsystem trigger. That gradient is handled prose-style in `false-positive-guide.md`, not as a code-load rule.
