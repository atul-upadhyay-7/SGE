# Contributing

## The merge rule

**No pull request merges without a green verification run.** This applies to
every contributor and every kind of change, including documentation, and it
applies equally to **AI-generated pull requests**: a machine-authored branch is
held to the same gate as a human one. There is no "trivial change" bypass, no
admin merge, and no citing a partial run as a pass.

A pull request is mergeable only when both of these are true:

1. `bash scripts/verify.sh` passes locally on the committed HEAD.
2. CI is green, including the required **`merge-gate`** status check
   (workflow `Verify BMS`).

`merge-gate` depends on every verification job (`lint`, `root-tests`,
`backend`, `frontend`, `browser`, `compose`) and fails if any of them fails,
is cancelled, or is skipped. It is the only status check that needs to be
required in branch protection, because it already aggregates the rest.

Maintainers: enable branch protection on `main` with **Require status checks
to pass before merging** and select `merge-gate` as the required check. Do not
allow bypassing the rule for administrators.

## Run the gate locally

```bash
bash scripts/verify.sh
```

The script clones the **committed** repository into a temporary directory (it
does not verify uncommitted working-tree changes), so commit first. It runs:

| Stage | What it proves |
|---|---|
| Dataset + pipeline | The NASA dataset downloads, checksums, and the pipeline reproduces its artifacts. |
| Clean-tree check | The pipeline does not mutate tracked files. |
| Root pytest suite | The offline ML tests pass. |
| Backend tests | The FastAPI suite passes against a **throwaway Postgres container**, with Alembic `upgrade head` and `alembic check`. |
| Ruff | `ruff check .` passes with the pinned version (`ruff.toml`). |
| Frontend | `npm ci`, unit tests, `tsc` typecheck, and the production build pass. |
| Numbers / docs sync | Every number in the generated documents is a rendered placeholder, and the documents match the generator output. |

Prerequisites for the full gate: Python 3.12 with `python3-venv`, Docker
(for the throwaway Postgres), and Node.js 20+ with npm. The backend and
frontend dependencies are installed into temporary directories, so they do not
pollute your environment.

A green run ends with:

```
verify.sh: ALL CHECKS PASSED
```

Paste that full output (not a summary) in the pull request description, or
link the green CI run. Reviewers should not approve without it.

### Partial runs are not a pass

For fast local iteration you can set `VERIFY_FULL=0` to skip the ruff,
backend, and frontend stages:

```bash
VERIFY_FULL=0 bash scripts/verify.sh
```

That run prints `PARTIAL (VERIFY_FULL=0) — NOT a merge-gate run`. It is useful
while developing, but it is **not** evidence for a merge. Re-run the full gate
on the final commit before requesting review.

## Pull request expectations

- Keep the change scoped; do not mix unrelated refactors with a fix.
- Describe what changed, why, and how it was verified.
- Include the exact command output for the verification you ran.
- If a test is flaky, fix or re-run it; do not disable it to get green.
- Never commit secrets, credentials, datasets, or generated model binaries
  outside the paths the repository already tracks.
