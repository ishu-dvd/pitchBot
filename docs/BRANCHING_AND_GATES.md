# Branching and Merge Gates

## Main branch protection
- Require pull requests.
- Require CI passing.
- Require at least one review.
- Prefer squash merge.

## Mandatory gates per PR
- Linting passes.
- Type checks pass.
- Unit/integration tests pass.
- No secrets detected.
- External side effects remain off unless explicitly scoped and approved.

## Worktree guidance
Use worktrees only for non-overlapping vertical slices.
