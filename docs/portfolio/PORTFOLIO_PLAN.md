# providence → portfolio artifact: plan (revised after skeptical review 2026-06-13)

> This is the **direction/rationale** doc. The step-by-step build instructions for a
> fresh agent live in `EXECUTION_PLAN.md`. Read this for *why*, that for *how*.

**Goal (the only success criterion):** a hiring manager spends ~90 seconds on a live URL or README and concludes *"this person can ship full-stack software AND do hard statistics."* NOT "make the product complete." Every scope call serves legible competence.

**Signals targeted:** full-stack / can-ship-product **and** data-ML / statistical rigor. **Budget:** ~1–2 weeks, one person (strong at stats, less so at frontend). **Hosting:** free (GitHub Pages). **Frontend repo:** separate `providence-dashboard`. **Stack:** React + Vite + TS + Tailwind + Recharts.

**Backend decision (2026-06-13):** static-export SPA **now**; a real FastAPI service is a **documented stretch goal (Phase 4)**, not in the core budget. The static path hits the budget + the stats signal; the API, only if reached, earns the harder full-stack signal honestly.

---

## What the skeptical review changed (don't re-litigate these)

Two independent critics reviewed the original plan. Net: the raw material is above portfolio-average; the risk is **presentation and over-scope**, not substance. Findings accepted:

1. **CRITICAL — point only at the canonical repo `C:\Users\epica\providence` (v0.3.0).** The frozen snapshot `Periapsis/tools/providence/` is **v0.2.0 and has NO judges**. One critic mis-read the snapshot and wrongly concluded the judge feature was fictional. It is real in canonical (`providence/judges.py`, the `examples/judged_faithfulness/` pack). A fresh agent must not repeat this.
2. **Drift has no history inside a single pack.** `results.json` is one run. The version-timeline needs `export` to call the existing `compare_packs(baseline, current)` across consecutive pack pairs and bake the rows in. `compare_packs` already does the Fisher+Holm math — export is a loop, not new statistics.
3. **Authoring believable history packs is real work** (~1 day): hand-tune mock pass-counts across versions so one version trips a Holm-significant Fisher drift event on tiny n. Was invisible in the original plan.
4. **5 custom-dataviz views in ~7 days is too much for a React-light builder.** Ship **fewer, finished** views. A half-done broad dashboard is worse than a small complete one for this goal.
5. **The statistics must be VISIBLE in the UI** — tooltips lifted from `stats.py` docstrings (why Wilson, why Fisher exact at n=5, why Holm). Otherwise the entire stats-rigor signal stays buried in the backend. Highest signal-per-hour add.
6. **Deploy skeleton FIRST, not last.** Get GitHub Actions → Pages publishing a stub on day 1 (Pages base-path config bites at end-of-budget otherwise).
7. **Pre-load the landing view on one compelling borderline case** — a "PASS but NOT settled" story legible in 5 seconds, with a plain-English verdict. This single screen demonstrates stats + product-judgment + design at once.

Findings rejected/demoted: judge feature is NOT fictional (snapshot confusion); schema "freeze" demoted from a milestone to a lightweight typed contract (keep, but small). The "drop the dashboard, write a viral 'this famous eval isn't statistically settled' post instead" idea is genuinely strong but is a *different* project — noted as an alternative, not the chosen path.

---

## Scope, locked (3 core views + stretch)

**Cut from the original:** per-item drilldown as its own view (→ inline expandable rows), the custom sample-size "gauge" (→ a sentence + thin progress bar), schema-freeze as a named milestone (→ a small typed contract done as a byproduct).

**Core (must ship, polished):**
- **V1 Overview / landing** — pre-loaded on the borderline example; one-line framing ("AI eval results, made statistically defensible"); pack series + headline verdicts.
- **V2 Suite detail** — Wilson CI as an **error bar** against the threshold line; **PASS vs PASS(point)** shown *visually* (e.g. solid vs hatched); sample-size as a sentence + thin bar; failing items as inline expandable rows. The small-sample-rigor showcase.
- **V3 Drift timeline** — the hero view: pass-rate + CI across versions, the Holm-significant Fisher event highlighted, plain-language "100%→75%, p=.04 Holm-adjusted, DRIFT."
- **Stats-credibility layer** — info tooltips from `stats.py` docstrings on every statistic.

**Optional 4th (only if core is finished and polished):**
- **V4 Judge panel** (data exists in canonical): agreement + settledness, pairwise κ, per-judge sensitivity bars, the JUDGE-DEPENDENT flag, rubric. Unusually rich; almost no portfolio has it. But it is the *first thing to drop* if time is tight.

**Stretch (Phase 4, documented, not budgeted):** FastAPI `POST /evaluate` (upload eval run → evidence pack) with validation + error handling, deployed free-tier. Earns the full-stack claim. Only if core + polish + README are done.

---

## The one risk to manage throughout

A generic dashboard reads as a bootcamp capstone and *caps perceived quality at the weakest visible layer* — fatal when the strong layer (stats) is invisible. Defenses, in priority: (1) the pre-loaded borderline story on first paint; (2) stats tooltips making the rigor visible; (3) domain-specific viz (error bars that are the actual interval), never a chart that could appear in any SaaS admin panel; (4) simple-and-clean beats ambitious-and-janky for a React-light builder.

## Sequencing rule

Deploy skeleton → ONE view (V2) rendering beautifully end-to-end on the live URL → then breadth. A deployed thin slice beats a broad local-only app. Stop and polish before adding V4/Phase 4.
