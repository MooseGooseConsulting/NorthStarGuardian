# 'manage_todo_list'

### Active
- [ ] P2: Performance & Observability (Drafting)
  - Files: `guardian/analyze.py` (caching), `guardian/cli.py` (tracing)
  - Why: Repeated diff analysis is slow; difficult to debug LLM token usage.

### Completed
- [x] Baseline setup and discovery - [522f153] - `ruff check` and `pytest` pass.
- [x] P1: Workstream 1: Core Reliability Hardening (Exceptions & Git) - [cd74234] - Added path injection check and improved error reporting in status handler.
- [x] P1: Workstream 2: CLI & I/O Test Suite Expansion - [2612e8c] - Improved `cli.py` coverage to 65% and added `tests/test_github_io.py`.
- [x] P2: Workstream 3: IDE & Developer Experience - [95e7d79] - Added `.vscode/settings.json` (ignored) and updated `README.md`.
- [x] P1: Workstream 4: Advanced Correctness & LLM Robustness - [f6e4a2b] - Atomic writes; robust JSON/regex parsing; `dateutil` integration.

### Blocked
- [ ] None

### Candidate backlog
- [ ] P1: Workstream 2: CLI & I/O Test Suite Expansion
  - Files: `tests/test_cli.py`, `tests/test_github_io.py`
  - Why: Coverage for `cli.py` (38%) and `github_io.py` (34%) is too low for a core pipeline.
  - Verification: `pytest --cov=guardian`
- [ ] P2: Workstream 3: IDE & Developer Experience
  - Files: `.vscode/settings.json`, `README.md`
  - Why: Missing IDE config; minor docs drift.
  - Verification: Manual check of help and settings.

### PR-sized workstreams
- [x] Workstream 1: Core Reliability Hardening
  - Commits: cd74234
  - Verification: `pytest tests/test_north_star_sources.py`
- [x] Workstream 2: CLI & I/O Test Suite Expansion
  - Commits: 2612e8c
  - Verification: `pytest --cov=guardian`
- [x] Workstream 3: IDE & Developer Experience
  - Commits: 95e7d79
  - Verification: Manual check.
- [x] Workstream 4: Advanced Correctness & LLM Robustness
  - Commits: f6e4a2b (local)
  - Verification: `pytest tests/test_analyze_robust.py tests/test_chronicle_robust.py`
  - Improvements: Atomic writes; robust JSON/regex parsing; `dateutil` integration.
