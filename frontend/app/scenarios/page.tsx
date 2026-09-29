"use client";
// 06 Scenarios: natural-language what-ifs → validated ScenarioSpec → re-solve → diff (T9.3).
import { PageHeader, Upcoming } from "@/components/ui";

const EXAMPLES = [
  "What if the distributor doubles its order from month 2?",
  "What if in-house capacity drops 20% for weeks 3–6?",
  "Freeze marketing spend in Mumbai.",
  "What if co-man is unavailable until March?",
  "Cut the marketing budget by 15%.",
  "Switch to growth mode.",
];

export default function ScenariosPage() {
  return (
    <div className="grid gap-5">
      <PageHeader
        title="Scenarios"
        lede="Ask a what-if in plain language. It is parsed into a structured, validated scenario spec, applied deterministically, re-solved and compared with the current plan."
      />
      <div className="rounded-lg border border-hair bg-surface p-5">
        <label htmlFor="whatif" className="text-[13px] font-medium">
          What if…
        </label>
        <div className="mt-2 flex gap-2">
          <input
            id="whatif"
            disabled
            placeholder={EXAMPLES[0]}
            className="flex-1 rounded-md border border-hair bg-surface-2 px-3 py-2 text-[14px] text-ink-3"
          />
          <button disabled className="rounded-md bg-ink px-4 py-2 text-[13px] font-medium text-[color:var(--surface)] opacity-40">
            Re-solve
          </button>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((e) => (
            <span key={e} className="rounded-full border border-hair px-2.5 py-1 text-[12px] text-ink-2">
              {e}
            </span>
          ))}
        </div>
      </div>
      <Upcoming phase="Phase 9 · T9.3" title="Scenario parser + executor">
        Supported levers: account volume change, new candidate account, capacity shock (% by weeks), co-man availability,
        budget change, mode change, regional spend freeze, SKU eligibility. A deterministic grammar parses supported phrasings
        first (works with no LLM); the Groq model is only a fallback constrained to the spec&apos;s JSON schema. Anything else
        returns &ldquo;unsupported&rdquo; with a reason. The LLM never edits numbers: the spec is applied in code and re-solved.
      </Upcoming>
    </div>
  );
}
