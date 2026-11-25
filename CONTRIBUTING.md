# Contributing

Thanks for helping! In short:

- Branch off **`dev`** and open your PR into `dev` (not `main`; `main` only holds released code).
- PR titles follow [Conventional Commits](https://www.conventionalcommits.org): `feat: …`, `fix: …`,
  `docs: …`, `ci: …`, `chore: …`; `feat!: …` marks a breaking change. The title ends up in the release notes.
- CI (shellcheck, unit tests, image build, smoke test) must pass. Run locally:
  `python3 -m unittest discover -s tests -p "test_*.py"` and `IMAGE=… tests/smoke.sh`.
- Fixes for a release being prepared go into `release/vX.Y`.

How branches, release candidates, releases and image tags work: [docs/RELEASING.md](docs/RELEASING.md).
