## Summary
Fixes broad exception swallowing for config initialization and timeline/dashboard slash commands to ensure errors are not masked. Resolves an injection vector in `git show` where branch names beginning with `-` could be misconstrued as command flags.

## Changes
- `guardian/cli.py`: Only catch specific exceptions or forward broad exceptions when loading configs so bad configs fail initialization out loud.
- `guardian/github_io.py`: Update exception handling around `repo.get_pull` to target `GithubException` instead of `Exception`.
- `guardian/north_star.py`: Add validation on `ref` prefix to prevent git flag injection (`ref.startswith("-")`).

## Tests and Verification
- `. .venv\Scripts\Activate.ps1 ; ruff check .` - All checks passed!
- `. .venv\Scripts\Activate.ps1 ; python -m pytest` - 251 passed in 3.23s

## Remaining Work
None.

## Assumptions
- Codebase does not use static type checking (mypy not configured in dev dependencies) and so we didn't add missing configurations.