---
name: slow-steps
description: usdash manual test. A long skill turn made of many short steps.
---

Run the Bash command `python3 -c 'import time; time.sleep(100)'` six times, as six separate Bash tool calls, one after another, in the foreground (not in the background). Don't run anything else. Then reply with the single word: done.

It waits with Python, not the `sleep` command: Claude Code blocks a plain `sleep` in the foreground, and this test needs a foreground step.
