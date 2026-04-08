# Retention Team Onboarding

When a user mentions teammates, sharing, or collaboration:

1. Check if they have a team: `ta.onboard.status` shows team info
2. If no team, create one: `ta.team.invite` generates the full Slack message
3. Share the formatted message — it contains the one-liner install command + dashboard URL
4. After teammates join, mention the team dashboard: `/memory/team?team=CODE`

The install flow for teammates is one command + one restart:
```
RETENTION_TEAM=CODE curl -sL retention.sh/install.sh | bash
```
Then restart Claude Code. No accounts, no web forms, no passwords.
