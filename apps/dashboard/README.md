# dashboard

Placeholder. Pełne scaffoldowanie Next.js 15 + shadcn/ui + React Flow zaplanowane w **fazie F6** (patrz `../../../LOCAL_APP_PLAN.md`).

Jedyny komponent w stack-u **Node** — reszta DAP to Python + uv. Dashboard pozostaje Next.js bo żadne Python-native UI nie podoła Pipeline Designerowi (React Flow + drag-drop + condition builder).

Docelowo zawiera:
- Runs list, Run detail z live polling state
- Pipeline Designer (React Flow + palette + inspector + condition builder)
- Agent registry + editor
- Node drawer z XML preview i state diff

Komunikuje się z `dap-engine` przez REST (`http://127.0.0.1:7333`).
