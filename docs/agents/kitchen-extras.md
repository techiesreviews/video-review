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
  does not collect) and the Node tests (`tests/*.test.mjs`), with no installs. GitHub runs the same
  script (`.github/workflows/checks.yml`: Node 24, no secrets, no deploy) on every pull request and on
  every push to `dev`; `kit pr` also runs them here for a code change, then lands after the review. A
  CSS-only change gets a review but no tests. A docs-only change (`paths.docs`) gets neither, as in every
  project.
  The browser checks (`tests/*-browser.mjs`) need Playwright and are run by hand: `npm run check:browser`.
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

- No preview deploys.
- **Keep LexTop current.** Lex records and edits on LexTop (Windows), whose checkout `D:\Projects\video-review`
  stays on `dev`. T3 pulls it by itself (the LexTop project has auto pull on). After a change lands on `dev`
  that Lex will use there, a lead checks on LexTop (T3 environment `LexTop`) that the landed commit is there:
  `git merge-base --is-ancestor <commit> HEAD` succeeds. If not, because LexTop was offline, the pull stopped, or auto pull is off, a
  helper on LexTop runs `git pull --ff-only origin dev`, and only when `git branch --show-current` says `dev`
  and `git status` shows no changed tracked files (its private untracked notes are fine). Anything in the way
  (another branch, local changes, its own commits) is reported to the lead, never switched, stashed, reset or
  overwritten. Notes only one machine needs (tool paths, the current Resolve project) go in
  `CLAUDE.local.md`, which git ignores.

## Release

- No release command is set up in the kit. Going live (`main`, the hosted site) needs Lex's say-so.
