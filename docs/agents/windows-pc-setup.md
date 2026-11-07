# Moving a Windows PC with a local kit onto the committed kit

For a PC that already has its own uncommitted kit files. Git will not overwrite them, so `dev` cannot be
checked out until they are moved. Run these in Git Bash, from the project's top folder.

1. See what is there: `git status`. If it lists a tracked file as changed (`.gitattributes`, `.gitignore`,
   `README.md`), commit or save that change first. Untracked private notes can stay where they are; do not
   commit them.
2. Move the local kit files out of the project:

   ```bash
   backup=../video-review-local-kit
   mkdir -p "$backup"
   for path in scripts/agents .claude/settings.json .codex/hooks.json AGENTS.md docs/agents docs/wayfinder; do
     if [ -e "$path" ]; then mkdir -p "$backup/$(dirname "$path")"; mv "$path" "$backup/$path"; fi
   done
   ```

3. Get the committed kit: `git fetch origin`, `git switch dev`, then `git pull --ff-only origin dev`. If the
   pull fails, stop: the local `dev` has its own commits, and the backup still holds the old kit.
4. List the project on this PC and check it: `scripts/agents/kit init`. It writes only what is missing, and
   ends with the result of `kit doctor`. If it writes any file, look at `git status` before going on: nothing
   from this PC belongs in a commit.
5. Put anything only this PC needs in `.claude/settings.local.json`, which git ignores, or in an environment
   variable. Copy only the lines you need from the backup, and no machine paths into committed files. The kit
   finds the project folder from git, so no path setting is needed.
6. Compare the backup's `docs/wayfinder`, `docs/agents` and `scripts/agents/project.d` with the committed
   copies. These may hold tickets, maps or commands that exist only on this PC. Land anything still wanted
   through `scripts/agents/kit pr`.
7. Delete `../video-review-local-kit` once the kit works and nothing in it is still wanted.
