# Contributing

Thanks for looking. The bar for this repo is unusual in one specific way:
**every statistical claim the tool makes must be backed by a stated
guarantee and a test.** PRs that add features without that backing will be
asked for it.

## Dev setup

```bash
git clone https://github.com/alejlizardi/providence
cd providence
pip install -e .
python -m unittest discover -s tests
```

No dependencies beyond `pyyaml`; the statistics are pure stdlib on purpose
(every number in a report should be recomputable by hand).

## House rules

1. **Statistical features ship as a triple**: the math (a short derivation
   or reference in the PR description), pinned unit tests (known values —
   e.g. Fisher p-values computed by hand, t-quantiles against standard
   tables), and, for anything affecting coverage/size/power claims, a
   validation check at the guarantee level.
2. **The LF rule is load-bearing.** Evidence packs are hash-pinned over
   exact bytes; `.gitattributes` forces LF so packs verify after checkout
   on every platform. Don't fight it, and don't commit files that depend on
   CRLF. CI verifies the committed example pack on Windows for exactly this
   reason.
3. **Honest vocabulary.** Packs are hash-pinned, not signed; `verify`
   catches accidents, not forgery. The mappings are interpretations, not
   the guidance text. Don't strengthen claims in docs that the code doesn't
   earn.
4. **The example must keep failing.** The committed worked example ships a
   failing suite and a use-limit violation on purpose — an evidence tool
   you only ever see passing is a demo, not evidence.

## Tests

`python -m unittest discover -s tests` must pass on Linux and Windows,
Python 3.10+. If you change anything under `providence/stats.py`, expect to
add known-value tests, not just behavioral ones.
