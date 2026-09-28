---
name: slow-tool
description: usdash manual test. A skill turn with one tool call longer than the 5-minute cache.
---

Run the Bash command `python3 -c 'import time; time.sleep(330)'` once, as a single Bash tool call in the foreground (not in the background) with a timeout of 400000 milliseconds. Don't run anything else. Then reply with the single word: done.

It waits with Python, not the `sleep` command: Claude Code blocks a plain `sleep` in the foreground, and this test needs a foreground step.
