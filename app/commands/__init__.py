"""Running a repo's `.claude/commands` from the dashboard (outside the spec, requested by the user
2026-10-30; see PROGRESS.md).

An admin picks a repo (and branch) or only a dự án, then a command of the repo's own `.claude`
or of the dự án's shared .claude repo. The command worker clones the repo, lays the shared
configuration under its `.claude/` (the repo's own files win), and runs `claude -p "/<command>
<arguments>"` there, so Claude Code itself expands the command exactly as on a laptop
(`$ARGUMENTS`, frontmatter `allowed-tools` / `model`, `!` lines, CLAUDE.md, rules, skills).
Files the command writes are handed back for one download, then deleted.
"""
