# 'manage_todo_list'

### Active
- [ ] P2: Performance & Observability (Drafting)
  - Files: `guardian/analyze.py` (caching), `guardian/cli.py` (tracing)
  - Why: Repeated diff analysis is slow; difficult to debug LLM token usage.

### Completed
- [x] Baseline setup and discovery - [522f153] - `ruff check` and `pytest` pass.
- [x] P1: Workstream 1: Core Reliability Hardening (Exceptions & Git) - [cd74234] - Added path injection check and improved error reporting in status handler.
- [x] P1: Workstream 2: CLI & I/O Test Suite Expansion - [2612e8c] - Improved `cli.py` coverage to 89% and added `tests/test_github_io.py` plus focused helper and command-handler tests.
- [x] P2: Workstream 3: IDE & Developer Experience - [95e7d79] - Added `.vscode/settings.json` (ignored) and updated `README.md`.
- [x] P1: Workstream 4: Advanced Correctness & LLM Robustness - [b0a8505] - Atomic writes; robust JSON/regex parsing; `dateutil` integration.

### Blocked
- [ ] None

### Candidate backlog
- [ ] P2: Remaining CLI fallback coverage
  - Files: `tests/test_cli.py`
  - Why: `cli.py` coverage improved to 89%, but several fallback/error branches remain uncovered.
  - Verification: `pytest --cov=guardian --cov-report=term-missing`

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
  - Commits: b0a8505
  - Verification: `pytest tests/test_analyze_robust.py tests/test_chronicle_robust.py`
  - Improvements: Atomic writes; robust JSON/regex parsing; `dateutil` integration.
