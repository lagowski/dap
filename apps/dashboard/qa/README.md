# Dashboard browser QA

Manual, non-destructive QA sweep of the DAP dashboard driven by the **Claude
Chrome extension** (Claude-in-Chrome). Files:

| File | What it is |
|------|------------|
| [`browser-qa-prompt.md`](./browser-qa-prompt.md) | The prompt to paste into Claude-in-Chrome. Self-contained: route map, per-page checks, safety rules, report format. |
| [`qa-report-template.md`](./qa-report-template.md) | Blank report (coverage table + known-issue confirmations + findings). |

## How to run

1. **Bring up the dashboard locally** (it lives on the production host; reach it
   over an SSH tunnel from your machine):
   ```bash
   ssh -N -L 3000:localhost:3000 -p 2222 dixter@192.168.1.100
   ```
   Leave that running, then open `http://localhost:3000` in Chrome.
   If you ever see `net::ERR_CONNECTION_REFUSED`, the tunnel dropped — re-run it.

2. **Log in manually** in the browser. Don't paste credentials into the agent
   chat — the prompt tells the agent to pause and let you log in.

3. **Open the Claude Chrome extension**, make sure the dashboard is the active
   tab, and **paste the entire contents of `browser-qa-prompt.md`**. Open
   DevTools → Console first so the agent can read console errors.

4. Let it run the full sweep top-to-bottom. At the end it produces a report; drop
   that into `qa-report-template.md` (or just keep the agent's output) and, for
   real bugs, comment on / open a GitHub issue.

## ⚠️ This is production

The prompt enforces **non-destructive** testing: no Run/Trigger, no
Save/Create/Archive/Delete/Revoke, no password or instance-settings changes, no
"Use this template". Forms are filled to test validation and then **cancelled**.
Don't loosen this unless you're pointing the tunnel at a throwaway environment.

## Scope

22 routes across Auth, Projects, Pipelines, Agents, Runs, Account/Settings, and
Admin. The sweep also **confirms the open issues in #681** (admin-header JSX,
table row clicks, `/agents/new` feedback, login engine badge, Import JSON file
dialog, `/agents/[id]/versions` 404) — in particular it nails down whether
**Import JSON** actually opens a file picker, which the source says it should.
