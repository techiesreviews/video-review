# Kitchen extras: video-review

`scripts/agents/kit rulebook` prints the shared rulebook, then this file. Where they differ, this
file wins.

## Branches, checks and the shared file

- **Landing branch `dev`, release branch `main`** (`branches` in `scripts/agents/kit.json`). A change
  lands on `dev` through `scripts/agents/kit pr`. `main` only takes `dev`, through
  `scripts/agents/kit pr --release --base main`.
- **This repo is public.** No client or sponsor names, tokens or personal data in files, commits or PR
  text. Private notes and recordings stay in the folders `.gitignore` keeps out (`acut/`, `input/`,
  `data/`).
- **Two machines.** The project is worked on from Linux and from Windows (Git Bash), so committed
  settings hold no machine paths: `scripts/agents/kit.json` has no `mainRoot`, and the kit finds the main
  checkout from git. A setting only one machine needs goes in `.claude/settings.local.json`, which git
  ignores, or an environment variable, such as `KIT_MAIN_ROOT`. A PC with its own uncommitted kit files
  moves them first: [windows-pc-setup.md](windows-pc-setup.md).
- **Checks:** `checks.test` runs `scripts/agents/run-tests.sh`: the Python tests (`unittest discover -s
  tests`, plus `tests/test_server.py` and `tests/test_mcp_server.py`, which are plain scripts discovery
  does not collect) and the Node tests (`tests/*.test.mjs`), with no installs. The repo has
  no GitHub CI workflow, so `kit pr` runs them here for a code change, then lands after the review. A
  CSS-only change gets a review but no tests. A docs-only change (`paths.docs`) gets neither, as in every
  project.
  `tests/listening-browser.mjs` needs Playwright and is run by hand.
- **`kit unit` is not set up here:** it takes one test runner and this project has two, so wherever the
  rulebook says `kit unit`, run `bash scripts/agents/run-tests.sh`.
- **Own commands:** none yet (`scripts/agents/project.d` is empty).
- **Shared file:** none is set.
- **Docs the shared rulebook names:** no runner guide, issue tracker guide or UX rulebook yet. How the
  tool works is in `README.md` and `docs/`.

## Visible changes

- **Feature list:** none. `paths.visible` is `web/`.
- **Screenshots:** no `kit shots` here; use a real browser on the local server.

## After a merge

- None: there are no preview deploys.

## Release

- No release command is set up in the kit. Going live (`main`, the hosted site) needs Lex's say-so.
