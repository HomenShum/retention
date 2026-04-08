# Retention After Code Change

When the user makes a code change (detected via git diff or explicit "I fixed X"):

1. Suggest verifying the fix: "Want to verify this didn't break anything? I can re-crawl your site."
2. Run `ta.crawl.url(url='...')` with the same URL from the last crawl
3. Compare findings — show what was fixed and what's new
4. Run `ta.savings.compare` to show the trajectory replay saved tokens vs a fresh crawl
5. If findings remain, suggest the next fix

Each re-crawl after a fix uses saved trajectories — getting cheaper every iteration.
This is the core retention.sh value loop: explore once, replay cheap forever.
