# DAP Dashboard QA Report

- **Date / time:**
- **Browser + version:**
- **Base URL:** http://localhost:3000
- **Build / commit under test:** (dashboard footer or `git rev-parse --short HEAD` on host)
- **Logged-in role:** (admin / operator)

---

## Coverage

- Routes visited: **__ / 22**
- Interactive elements exercised: **__**
- Not exercised (destructive, deliberately skipped): **__**

---

## Summary table

| Route | Renders | Console clean | Interactions tested | Verdict |
|-------|:------:|:------:|---------------------|---------|
| /login | | | | |
| /signup | | | | |
| /forgot-password | | | | |
| /reset-password | | | | |
| / (home) | | | | |
| /projects | | | | |
| /projects/new | | | | |
| /projects/[id] | | | | |
| /projects/[id]/edit | | | | |
| /pipelines | | | | |
| /pipelines/new | | | | |
| /pipelines/[id]/edit | | | | |
| /agents | | | | |
| /agents/new | | | | |
| /agents/[id] | | | | |
| /agents/[id]/edit | | | | |
| /runs | | | | |
| /runs/[id] | | | | |
| /account | | | | |
| /settings | | | | |
| /admin | | | | |
| /admin/users | | | | |
| /admin/audit-log | | | | |
| /admin/api-tokens | | | | |
| /admin/settings | | | | |

Legend: ✅ ok · ⚠️ minor · ❌ broken · — not reached

---

## Known-issue confirmations (issue #681)

| # | Claim | Result | Evidence |
|---|-------|--------|----------|
| 1 | Admin headers show raw `$<span…>` JSX (audit-log, api-tokens) | CONFIRMED / NOT REPRO | |
| 2 | Table name unclickable in cell padding (/projects, /agents); /pipelines name not a link | | |
| 3 | /agents/new — no scroll/feedback; `ZodError: Name is required` in console | | |
| 4 | /login amber "Engine busy or unavailable" flashes (by-design) | | |
| 5 | **Import JSON** opens no file dialog (pipelines, agents) — does the OS picker appear? | | |
| 6 | /agents/[id]/versions → 404 | | |

---

## New findings

### [🔴/🟠/🟡/❓] <title> — <route>
- **Repro:**
- **Expected:**
- **Actual:**
- **Console:**
- **Screenshot:**

*(repeat per finding)*

---

## Not exercised (potentially destructive — skipped on purpose)

- e.g. Run/Trigger on pipelines & /runs
- Archive on projects/pipelines/agents
- Save on edit forms
- API-token create/revoke, user role changes, instance-settings save
- "Use this template" on /pipelines/new
