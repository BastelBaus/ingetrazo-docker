# Branches, releases and images

```
feature/*, fix/* --PR--> dev --> release/vX.Y --PR--> main --> tag vX.Y.Z
                          ^      (fixes only,          |
                          |       rc per push)         |
                          +------ back-merge PR <------+
hotfix/* (from main) --PR--> main --> tag vX.Y.Z+1 --> back-merge into dev
```

| Branch / tag | What it is | Image (`ghcr.io/bastelbaus/ingetrazo`) |
|---|---|---|
| `dev` | Integration branch, default branch, target of all feature PRs | `:dev`, `:dev-<sha>` (amd64) |
| any branch, manual *Publish* run | Try a branch in a container | `:br-<branch>` (amd64) |
| `release/vX.Y` | Release being stabilised: fixes only | `:X.Y.Z-rc.N`, `:rc` |
| `main` + tag `vX.Y.Z` | Released code only | `:X.Y.Z`, `:X.Y`, `:stable`, `:latest` |

Release images are built for amd64 and arm64, everything else for amd64 only (faster).
`dev-*` and `br-*` images are deleted after 30 days (*GHCR cleanup*).

## Everyday work

1. Branch off `dev`: `feature/…`, `fix/…`, `docs/…`.
2. Open a PR into `dev`. The title follows [Conventional Commits](https://www.conventionalcommits.org):
   `feat: …`, `fix: …`, `docs: …`, `ci: …`, `chore: …`, `feat!: …` for breaking changes.
   It becomes the commit message (squash merge) and the line in the release notes.
3. CI must pass. After the merge, *Publish* builds `:dev`.

To try an unmerged branch in a container: *Actions → Publish → Run workflow* on that branch → `:br-<branch>`.

## Release

1. *Actions → Release start → Run workflow*, version `X.Y`. This creates `release/vX.Y` from `dev`,
   tags `vX.Y.0-rc.1` (pre-release, image `:rc`) and opens the PR `release/vX.Y → main`.
2. Test the candidate. Fixes: PR into `release/vX.Y`; each merge makes the next `-rc.N`.
   `dev` goes on meanwhile.
3. When a candidate is good: merge the release PR with **Create a merge commit**.
   *Release finalize* tags `vX.Y.0` on `main`, writes the GitHub release, publishes `:stable` / `:latest`
   and opens the back-merge PR `main → dev`. Merge that one with a merge commit too.

The release branch may stay for patch releases: further pushes make `vX.Y.1-rc.N`, and the next
release PR from it becomes `vX.Y.1`.

## Hotfix

Branch `hotfix/…` off `main`, PR into `main`, merge (merge commit). *Release finalize* makes the next
patch release and the back-merge PR into `dev`.

## Merge methods

| PR | Method |
|---|---|
| feature → `dev`, fix → `release/*` | Squash |
| `release/*` → `main`, `hotfix/*` → `main`, back-merge `main` → `dev` | Merge commit |

## Repository settings this relies on

- Default branch `dev`; *Automatically delete head branches* on; squash and merge commits allowed.
- Rulesets: `main` and `release/*`: PR required, status check *smoke*, 1 approval for `main`.
  `dev`: PR required, status check *smoke*. Tags `v*`: only maintainers may create or delete.
- *Actions → General*: *Allow GitHub Actions to create and approve pull requests*.
- Optional secret `RELEASE_TOKEN` (fine-grained token: contents + pull requests read/write):
  release and back-merge PRs opened with it get CI runs; with the default token they do not.
