# Retention First Run

When retention.sh MCP tools are available but no runs exist yet:

1. Run `ta.onboard.status` to check prerequisites
2. Suggest crawling the user's own project URL: `ta.crawl.url(url='http://localhost:PORT')` or their deployed site
3. Show the findings clearly — JS errors, rendering issues, a11y gaps
4. After findings shown, suggest fixes with file paths
5. After user applies fix, suggest re-crawl: `ta.crawl.url(url='...')`
6. After re-crawl, show savings: `ta.savings.compare`

The goal is to get the user from "just installed" to "seeing their own data" in under 5 minutes.
