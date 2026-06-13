# EXECUTION PLAN — evidentry-dashboard (portfolio artifact)

**For:** a fresh Claude Code instance starting cold. Read this top to bottom before acting.
**Companion:** `PORTFOLIO_PLAN.md` (the *why* + the skeptical-review outcomes). This file is the *how*.

---

## 0. Orient yourself first (do this before writing anything)

**The goal is a resume artifact, not a finished product.** Success = a hiring manager spends ~90 seconds on a live URL / README and concludes "this person can ship full-stack software AND do hard statistics." Optimize every decision for *legible competence in 90 seconds*, not completeness. A small, polished, deployed thing beats a broad, half-done one. If you are ever choosing between "another view" and "make the existing views clean + deployed," choose clean + deployed.

**CRITICAL repo fact — do not get this wrong:**
- The **canonical** evidentry is `C:\Users\epica\evidentry` — **v0.3.0**, has the judge feature (`evidentry/judges.py`), the judged example pack, 150 tests. **All backend work happens here.**
- `C:\Users\epica\Periapsis\tools\evidentry\` is a **FROZEN v0.2.0 SNAPSHOT with NO judges.** Do **not** read it to understand current capability and do **not** edit it. (A prior reviewer mis-read it and wrongly concluded the judge feature didn't exist. Don't repeat that.)

**Confirm before starting** (sanity, ~1 min):
```bash
cd /c/Users/epica/evidentry
grep version pyproject.toml | head -1            # expect 0.3.0
ls evidentry/judges.py                            # exists
.venv/Scripts/python.exe -m pytest -q | tail -2   # expect ~150 passed
```
If the venv path differs, find the interpreter that has pytest; tests must pass green before you change backend code.

**Two new repos/locations you will create:**
- Backend changes: in-place in `C:\Users\epica\evidentry`.
- Frontend: a **new separate repo** `evidentry-dashboard` (keeps the published library clean). Suggested location `C:\Users\epica\evidentry-dashboard`.

**Discipline:** this is a portfolio build, not the startup. Don't add product features (auth, DBs, new statistics, live provider calls). Don't gold-plate. When tempted to expand scope, re-read the goal.

---

## How evidentry works (the facts the plan relies on — all verified)

- A run is driven by an `evidentry.yaml` (model card + provider + suites). `provider: mock` makes the whole pipeline deterministic with **no API key** — each dataset item carries the output it would have produced. Use mock for everything here.
- `evidentry run -c evidentry.yaml` writes an **evidence pack** dir: `report.md`, `results.json`, `manifest.json`. **`results.json` is the render-ready payload** the frontend consumes. Its shape (verified): `model` (card), `provider`, `statistics`, `suites[]`, `summary`. Each suite has `pass_rate, n_passed, n_items, threshold, verdict` (`PASS` / `PASS (point)` / `FAIL` / `FAIL (point)`), `ci95_low, ci95_high, ci_method`, `sample_size_certificate`, `items[]`, and — for judge suites — a rich `judge_evidence` block (`agreement` incl. `settledness`, `pairwise[]` with `kappa`, `per_judge[]`, `judge_dependent`, `n_invalid_responses`, `rubric`).
- **Drift/history is NOT inside a single pack.** It is computed pairwise by `compare_packs(baseline_pack_dir, current_results_dict)` in `evidentry/evidence.py`, which returns `{"method", "suites":[{"suite","rate_a","rate_b","p_value","p_holm","significant","comparable","reason"?}]}` using Fisher exact + Holm. The version timeline must call this across consecutive packs and store the rows.
- The statistical reasoning worth surfacing in the UI lives in `evidentry/stats.py` docstrings (Wilson small-sample behavior; Fisher "anti-conservative z-test at n=4–8"; Holm "~40% false-drift without adjustment"; Clopper-Pearson exactness; sample-size certificate semantics). **Lift tooltip copy from there.**

---

## PHASE 0 — Backend prep (in `C:\Users\epica\evidentry`). Target ~2.5 days.

### 0.1 `evidentry export` subcommand  (~0.5 day)
Add a `cmd_export` in `evidentry/cli.py` (mirror the existing `cmd_diff` / `cmd_run` structure; register a subparser like the others).

- **Input:** one or more evidence-pack directories (a "series"), in version order, e.g. `evidentry export <pack1> <pack2> ... -o site-data/`.
- **Output (static, frontend-ready):**
  - `site-data/packs/<pack_id>.json` — the pack's `results.json` verbatim (it's already the payload).
  - `site-data/index.json` — an array, one entry per pack: `{id, model_name, version, generated_at, suites_passed, total_suites, headline_verdict}` (derive from each pack's `summary`/`model`). Ordered.
  - `site-data/drift.json` — for each consecutive pair `(packs[i], packs[i+1])`, call `compare_packs(packs[i]_dir, packs[i+1]_results)` and store `{from_version, to_version, rows: <compare_packs result["suites"]>}`. This is the timeline data. Reuse `compare_packs`; do **not** re-implement statistics.
- **Tests:** add `tests/test_export.py` — export the two existing example packs, assert `index.json` length, assert `drift.json` rows exist and that a known-drifting pair is `significant: true`. Keep the 150→~155 green.
- Keep it pure-stdlib + existing imports (matches the codebase).

### 0.2 Authoring the history series  (~1 day — the real cost, don't underestimate)
Create `examples/model_history/` — ONE model across **3–4 versions**, all `provider: mock`, deterministic. This is the dataset that makes the drift timeline a *story*.

- Base it on the existing `examples/credit_memo_summarizer/` config + datasets (copy, then vary).
- Across versions, move mock pass/fail outcomes so that: most suites stay stable, and **exactly one suite at one version trips a Holm-significant Fisher drift event** (e.g. a refusal/accuracy suite drops 100%→~62% on n≈8). Because Fisher on tiny n is conservative, you will hand-tune counts — verify with `evidentry diff <prev> <curr>` until you see `[DRIFT]` with `holm < .05`. Also engineer **one borderline `PASS (point)`** suite (point estimate over threshold, Wilson lower bound under it) for the landing story.
- Generate the packs: `evidentry run -c <each version's yaml>`, collect the pack dirs in order.
- Commit the configs, datasets, and generated packs so CI can regenerate them.

### 0.3 Typed contract (lightweight — a byproduct, not a milestone)  (~0.5 day)
- Emit/write a `schema/results.schema.json` (JSON Schema) describing `results.json`, and a short `schema/README.md`. This is the contract the frontend's TS types mirror. Don't over-invest — one focused pass. (Signal: schema discipline; practical: stops the frontend chasing a moving target.)

**Phase 0 done when:** `evidentry export examples/model_history/packs/* -o site-data/` produces `index.json` + `packs/*.json` + `drift.json` with a real significant-drift row and a borderline PASS(point) suite; tests green.

**Do NOT in Phase 0:** schema "freeze" ceremony beyond the above; any FastAPI; new metrics/stats; touching the snapshot in `tools/`.

---

## PHASE 1 — Deploy skeleton + first real view. Target ~2 days.

### 1.1 Scaffold + deploy a stub FIRST  (~0.5 day)  ← do this before building views
- `npm create vite@latest evidentry-dashboard -- --template react-ts` in `C:\Users\epica\`. Add Tailwind (official Vite guide) + Recharts. `git init`, push to a new GitHub repo `evidentry-dashboard`.
- Add `.github/workflows/deploy.yml`: build the SPA and publish to **GitHub Pages**. Set Vite `base: '/evidentry-dashboard/'` (Pages serves under the repo path — getting this wrong is the classic blank-page bug). Confirm the **stub** is live at `https://<user>.github.io/evidentry-dashboard/` before writing any real UI.
- Vendoring the data: simplest is to **copy `site-data/` into the frontend's `public/data/`** as a committed build input (a CI step can regenerate it via `pip install -e ../evidentry && evidentry export ...`, but committing it first guarantees a working demo). The app `fetch`es `import.meta.env.BASE_URL + 'data/index.json'`.

### 1.2 V2 Suite detail, end-to-end, polished  (~1.5 day)  ← prove the whole pipe with the highest-value view
Build the **suite-detail view first** (it's the stats showcase and validates data-loading + charts + deploy together):
- Load a pack, list its suites. For a suite render: pass rate, **Wilson CI as a horizontal error bar** with the **threshold as a vertical line** (Recharts `ErrorBar` or a custom SVG bar — keep it correct: the bar spans `ci95_low..ci95_high`, the dot at `pass_rate`).
- **PASS vs PASS(point) shown visually** (e.g. solid fill = settled PASS/FAIL, hatched/outlined = `(point)`), not just a text label. This distinction is the single most distinctive idea in the project — make it unmissable.
- Sample-size certificate as **one plain sentence + a thin progress bar** ("settled needs ~N items; you have n") — NOT a custom gauge.
- Failing items as **inline expandable rows** (input / output / expected) — not a separate routed view.
- **Stats tooltips**: an `(i)` on each statistic with copy adapted from `stats.py` docstrings. This is what makes the rigor visible.

**Phase 1 done when:** the live Pages URL shows the real suite-detail view for a real pack, error bars + settledness legible, tooltips present.

---

## PHASE 2 — The other two core views. Target ~2.5 days.

### 2.1 V1 Overview / landing  (~1 day)
- Pre-load on the **borderline "PASS but NOT settled" story** from 0.2. Top of page, one plain-English verdict line, e.g.: *"Passes at 94% (target 90%) — but NOT settled: 95% CI [88%, 97%], ~140 more items needed. Don't ship yet."* Legible in 5 seconds.
- One-sentence framing header ("AI eval results, made statistically defensible").
- The pack series as cards/list with headline verdicts; click → suite detail.

### 2.2 V3 Drift timeline (the hero view)  (~1.5 day)
- Consume `drift.json`. Plot each suite's pass rate (with CI band) across versions; **highlight the Holm-significant Fisher event** (color + a flag marker).
- Plain-language callout on the event: "factual_accuracy: 100%→75%, Fisher p=.04 (Holm-adjusted), flagged DRIFT."
- Tooltip explaining why Fisher exact (anti-conservative z-test at n=4–8) + why Holm (family-wise control) — from `stats.py` docstrings.

**Phase 2 done when:** all three core views are live, navigable, and clean.

---

## PHASE 3 — Ship & polish. Target ~1.5 days.  (do NOT skip — this is where the 90-second impression is won or lost)

- **README (the artifact that gets read):** first screen = one-sentence what + one screenshot/GIF + live link + 3 bullets of "what's impressive here" (published PyPI library; hand-rolled validated statistics; the settledness idea). Methodology + stack details **below the fold**. Link `evidentry-dashboard` ⇄ `evidentry` both ways.
- **Screenshots/GIFs** of the borderline-story landing + the drift event.
- **Polish pass:** responsive, no alignment/overflow bugs, real `<title>`/favicon (reuse the SVG logos in `Periapsis/Logos/` if suitable), tasteful color, empty/error states. For a React-light builder: **simple-and-clean > ambitious-and-janky.** A mediocre frontend caps the perceived quality of everything; spend the polish budget here, not on a 4th view.

---

## PHASE 4 — STRETCH (only if Phase 0–3 are done AND polished). Optional.

Pick at most one, in this order of value:
1. **V4 Judge-panel view** — render the canonical judged pack's `judge_evidence`: agreement + settledness, pairwise **κ**, per-judge sensitivity bars, the **JUDGE-DEPENDENT** flag, rubric. Rich and rare. (First thing to cut if time is tight.)
2. **FastAPI backend (earns the full-stack claim honestly):** a small service `POST /evaluate` that accepts an uploaded eval run (dataset+outputs JSONL or a config) and returns the evidence pack JSON, with **request validation + proper error responses + status codes**; one Dockerfile; deploy to a free tier (Fly.io/Render). Wire one "run live" button in the UI to it. Document it in the README. This converts "renders static JSON" into "designed & deployed an API" — the one thing the static SPA can't claim. Budget honestly ~2–3 days; do not start it unless the core is genuinely finished.

---

## Definition of done (the bar to hit, in priority order)

1. A **live GitHub Pages URL** that loads instantly, pre-shows the borderline "not settled" story, and is legible to a non-statistician in 90 seconds.
2. Three clean views; CIs rendered as real error bars; the PASS-vs-PASS(point) distinction visually obvious; the drift event highlighted with a plain-English explanation.
3. Stats reasoning **visible** via tooltips (rigor not buried).
4. A README whose first screen sells it; both repos cross-linked; the PyPI library and validation study surfaced.
5. Backend `evidentry export` merged with tests green in canonical evidentry.

If you run low on time, **cut breadth (V4, Phase 4, extra polish features), never cut: deploy, the borderline story, the stats tooltips, or the README first screen.** Those four carry the 90-second impression.

## Anti-goals (re-read if scope starts creeping)

- No auth, no database, no multi-tenancy, no new statistical methods, no live provider/API-key handling in the browser.
- No editing the frozen snapshot in `Periapsis/tools/evidentry/`.
- No 5th view, no "while I'm here" library refactors.
- Not trying to make evidentry a real product. This is a portfolio piece.
