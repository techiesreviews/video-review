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
  checkout from git. A setting only one machine needs goes in `.claude/settings.local.json` or an
  environment variable, such as `KIT_MAIN_ROOT`.
- **Checks:** `checks.test` runs `scripts/agents/run-tests.sh`: the Python tests
  (`unittest discover -s tests`) and the Node tests (`tests/*.test.mjs`), with no installs. The repo has
  no GitHub CI workflow, so `kit pr` runs them here and lands after the review.
  `tests/listening-browser.mjs` and `npm run check:browser` need Playwright and are run by hand.
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
