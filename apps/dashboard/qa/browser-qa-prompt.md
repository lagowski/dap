# DAP Dashboard — Browser QA Sweep (paste into Claude-in-Chrome)

> Paste everything below the line into the Claude Chrome extension while the DAP
> dashboard is open in the active tab. It is self-contained: route map, per-page
> checks, safety rules, and the report format are all included.

---

You are a meticulous QA engineer driving a real browser. Your job is to exercise
**every page and every interactive element** of the DAP dashboard, confirm what
works, and surface anything broken — without changing or destroying any data.

## 0) Environment

- **Base URL:** `http://localhost:3000` (the app is reached through an SSH tunnel
  to the production host). If the tab isn't already on the dashboard, navigate there.
- This is a **single-page Next.js app**. After each navigation, wait for network
  to settle before judging the page.
- **Open DevTools → Console** and keep it visible. A page is only "clean" if it
  loads with **no red console errors** and no `net::ERR_CONNECTION_REFUSED`
  (that last one means the tunnel dropped — stop and tell the operator).

## 1) ⚠️ Safety — this is PRODUCTION. Non-destructive testing only.

Treat all data as real and irreplaceable. You MAY: navigate, read, hover, open
menus/dialogs, type into form fields, click "Validate", switch tabs, click table
**row names** (to test navigation), and **Cancel** out of dialogs.

You MUST NOT click / confirm any of these (they mutate state):

- **Run / Trigger run** on any pipeline or project (do not start engine runs)
- **Save / Update / Create** that writes a real record (open the form, fill it to
  test validation, then **Cancel** — never submit)
- **Archive / Delete / Revoke / Disable**
- **Promote**, **Approve/Reject** a gate, change a **password**, create/revoke an
  **API token**, create/disable a **user**
- Anything under **Admin → Settings (instance settings)** beyond reading
- **"Use this template"** on `/pipelines/new` (it creates a real pipeline) — you
  may open the gallery and inspect, but do not confirm creation

If you're unsure whether a control mutates state, **don't click it** — note it as
"not exercised (potentially destructive)" and move on.

To test forms safely: fill fields with obvious throwaway values (e.g. name
`ZZ-qa-do-not-save`), confirm inline validation behaves, then **Cancel**.

## 2) Login

Do **not** ask for or store credentials in this chat. If the app shows the login
page, **pause and ask the operator to log in manually** in the browser, then
continue once you see the authenticated shell. On `/login` itself, still check:
the email/password fields, the engine-status badge, the "forgot password" and
"sign up" links, and that submitting wrong credentials shows an error (you may use
a deliberately wrong throwaway password for that one check).

## 3) Method — for each route below

1. Navigate to it; wait for load.
2. **Render:** does the page render fully (no blank/spinner-stuck, no raw
   `{template}` / `<span>` text leaking into the UI)?
3. **Console:** capture any red errors/warnings.
4. **Enumerate** every interactive element (links, buttons, tabs, dropdowns,
   filters, form fields, dialogs, table rows).
5. **Exercise** each non-destructive element and record the result.
6. **Navigation:** confirm links go where their label implies. For tables, test
   clicking the **row/name** specifically in the *padding/whitespace* of the cell,
   not just dead-center on the text (known suspced bug).
7. **Evidence:** on any failure, capture a screenshot and the exact console text.

## 4) Routes & elements to cover

**Auth**
- `/login` — email, password, submit (wrong-pass error only), engine badge, links to forgot-password & signup
- `/signup`, `/forgot-password`, `/reset-password` — fields render, client validation fires on empty/invalid input (do not actually submit real requests)

**Global shell** (after login)
- Sidebar/top nav: every link reaches the right section; active state; user menu; project scope picker; engine/db status pill

**Projects**
- `/projects` — table **row→detail navigation**, project-scope dropdown, "+ New project", per-row **Edit** link, **Archive** (do NOT click)
- `/projects/new` — form fields + validation, **Cancel**
- `/projects/[id]` — workspace tabs: workflows, **env vars**, recent runs, triggers (read only; do not add/save env vars)
- `/projects/[id]/edit` — full edit form (fill → Cancel, don't Save)

**Pipelines**
- `/pipelines` — table row→nav, **Import JSON** (does the OS file dialog open? — this is specifically under suspicion, confirm yes/no), per-row **Run/Edit/Archive** (only Edit-navigation is safe; do NOT Run/Archive)
- `/pipelines/new` — template gallery renders; inspect a template; do NOT "Use this template"
- `/pipelines/[id]/edit` — React-Flow editor loads, Inspector panel, click nodes, **Validate** button (safe; expect "Pipeline is valid" or errors). Do NOT Save.

**Agents**
- `/agents` — table row→nav, **Import JSON** (file dialog opens?), **New agent**, per-row **Edit**, **Archive** (no)
- `/agents/new` — template picker; **"Start from scratch"** and selecting a template: does the form below get focus/scroll, or does nothing visibly happen? Note any `ZodError` in console. Fill → Cancel.
- `/agents/[id]` — details: prompt, runtime config, contracts, versions section. Also try `/agents/[id]/versions` directly — expected 404 (confirm).
- `/agents/[id]/edit` — Form tab + **Test** tab dry-run. (A dry-run may call the engine — run **at most one** small dry-run only if the operator OKs it; otherwise just confirm the tab renders.)

**Runs**
- `/runs` — list + filters: **Project, Pipeline, From, To, Status**; "Trigger run" dialog (open + **Cancel**, do NOT trigger)
- `/runs/[id]` — timeline, React-Flow diagram, click nodes → **Output / Prompt / State diff** tabs, live-progress indicators

**Account / Settings**
- `/account` — profile fields; change-password form (fill → do NOT submit)
- `/settings` — read-only operator view renders

**Admin**
- `/admin` — 4 section cards, each links correctly
- `/admin/users` — list renders; per-row actions present (do NOT change roles/disable)
- `/admin/audit-log` — data + filters; **check the section header renders a number, not literal `$<span …>{total…}</span> events`**
- `/admin/api-tokens` — token list; **same header check** (should say "N tokens", not raw markup); do NOT create/revoke
- `/admin/settings` — instance settings render (read only; do NOT save)

## 5) Known issues to CONFIRM (don't just re-report — verify true/false)

1. **Admin headers show raw JSX** on `/admin/audit-log` & `/admin/api-tokens` (literal `$<span…>{total.toLocaleString()}</span> events/tokens`). → Confirm present.
2. **Table row name not clickable in padding** on `/projects` & `/agents` (link works only on the exact text); `/pipelines` name may not be a link at all. → Confirm.
3. **`/agents/new`**: no scroll/feedback after picking a template / "Start from scratch"; `ZodError: Name is required` in console. → Confirm.
4. **Login** shows amber "Engine busy or unavailable" briefly. → Confirm (this is by-design; just note if it flashes when the engine is actually fine).
5. **Import JSON** (pipelines & agents) reportedly does nothing. The code looks correctly wired — **specifically verify whether the native file-picker dialog opens** when clicked, and capture console. This one matters most.
6. **`/agents/[id]/versions` → 404** (versions are shown inline on `/agents/[id]`). → Confirm.

## 6) For every finding, record

- Route + element (and a stable selector/label)
- Steps to reproduce
- Expected vs actual
- Console output (verbatim) and a screenshot
- Severity: 🔴 high / 🟠 medium / 🟡 low / ❓ needs-info
- Whether it's destructive-blocked (you couldn't fully test it safely)

## 7) Output — produce this report at the end

```
# DAP Dashboard QA Report — <date/time>, browser <name+version>

## Coverage
Routes visited: X / 22.  Elements exercised: N.  Not exercised (destructive): M.

## Summary table
| Route | Renders | Console clean | Interactions | Verdict |
|-------|---------|---------------|--------------|---------|
| /projects | ✅ | ✅ | row-nav ❌, scope ✅, new ✅ | issues |
| ... | | | | |

## Known-issue confirmations (1–6)
1. Admin header raw JSX — CONFIRMED / not reproduced (evidence)
... (one line each)

## New findings
### [severity] <title> — <route>
- Repro / Expected / Actual / Console / Screenshot

## Not exercised (potentially destructive)
- <list of buttons you deliberately did not click>
```

Work through the routes **in order**, top to bottom. Don't stop at the first bug —
complete the full sweep, then deliver one consolidated report. If the tunnel
drops (connection refused) at any point, stop and tell the operator how to restore
it (`ssh -N -L 3000:localhost:3000 -p 2222 dixter@192.168.1.100`).
