"""Stetho, the dashboard's AI assistant (outside the spec, requested by the user 2026-10-31; the
idea comes from the AI assistant of vhs-infrastructure-monitor, see PROGRESS.md).

Admins and managers ask questions about the screen they are on; the answer streams back.
`context` builds the text the model sees (only what the asker may already see), `chat` runs one
turn through the Claude Code CLI with every tool off.
"""
