"use client";

/**
 * Form / Test tab switcher used on the agent New + Edit pages.
 *
 * Uses native ``role=tablist``/``role=tab``/``aria-selected``
 * semantics so screen readers announce the active tab correctly.
 * The corresponding panels live in the parent (one is hidden via
 * ``className`` so neither resets state on switch); we expose the
 * generated panel ids via ``aria-controls`` to wire them up.
 */

const TAB_LABELS: Record<AgentTab, string> = {
  form: "Form",
  test: "Test",
};

export type AgentTab = "form" | "test";

interface AgentTabsProps {
  tab: AgentTab;
  onTabChange: (next: AgentTab) => void;
  formPanelId: string;
  testPanelId: string;
}

export function AgentTabs({
  tab,
  onTabChange,
  formPanelId,
  testPanelId,
}: AgentTabsProps) {
  const panelIdFor: Record<AgentTab, string> = {
    form: formPanelId,
    test: testPanelId,
  };
  return (
    <div className="border-b">
      <div role="tablist" aria-label="Agent editor sections" className="-mb-px flex gap-4">
        {(Object.keys(TAB_LABELS) as AgentTab[]).map((key) => {
          const active = tab === key;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={panelIdFor[key]}
              tabIndex={active ? 0 : -1}
              onClick={() => onTabChange(key)}
              className={
                active
                  ? "border-b-2 border-primary px-1 pb-2 text-sm font-medium text-foreground"
                  : "border-b-2 border-transparent px-1 pb-2 text-sm font-medium text-muted-foreground hover:text-foreground"
              }
            >
              {TAB_LABELS[key]}
            </button>
          );
        })}
      </div>
    </div>
  );
}
