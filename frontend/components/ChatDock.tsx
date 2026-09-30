"use client";
// "Ask the plan": a chat drawer on every screen (PRD US5). What-ifs are parsed and re-solved;
// questions are answered by the configured LLM from this run's facts, every number checked.
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { modeLabel } from "@/lib/format";
import { useSelection } from "@/lib/selection";

type Msg = {
  role: "user" | "assistant";
  content: string;
  source?: "llm" | "rules" | "facts";
  model?: string | null;
  note?: string;
  scenario?: { run_id: string; changes: string[] };
  context?: string;
};

const SUGGESTIONS = [
  "Why is there a shortfall risk, and what can we do?",
  "What if the distributor doubles its order from month 2?",
  "What if capacity drops 30% in weeks 3-6?",
  "Which B2B accounts are not fully served?",
  "What if we lose co-manufacturing?",
];

const SOURCE: Record<string, string> = {
  llm: "AI answer · numbers checked against the plan",
  rules: "re-solved by the optimizer",
  facts: "quoted from the plan",
};

export function ChatDock() {
  const { runId, world, mode, payload, setScenario } = useSelection();
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const context = `${world ?? "—"} · ${modeLabel(mode)}${payload?.run.kind === "scenario" ? " · scenario" : ""}`;

  useEffect(() => {
    void end.current?.scrollIntoView({ behavior: "smooth" }); // may return a Promise in new browsers
  }, [msgs, busy]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || !runId || busy) return;
    const last = [...msgs].reverse().find((m) => m.context)?.context;
    const next: Msg[] = [...msgs, { role: "user", content: q, context: last === context ? undefined : context }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    try {
      const r = await api.chat(
        runId,
        next.slice(-12).map(({ role, content }) => ({ role, content })),
      );
      setMsgs((m) => [...m, { role: "assistant", content: r.reply, source: r.source, model: r.model, note: r.note, scenario: r.scenario }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: `Sorry, that failed: ${e instanceof Error ? e.message : String(e)}` }]);
    } finally {
      setBusy(false);
    }
  };

  if (!open)
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full bg-ink px-4 py-2.5 text-[13.5px] font-medium text-[color:var(--surface)] shadow-lg hover:opacity-90"
      >
        <span className="inline-block h-2 w-2 rounded-full bg-brand" />
        Ask the plan
      </button>
    );

  return (
    <aside
      aria-label="Ask the plan"
      className="fixed bottom-0 right-0 top-0 z-40 flex w-full max-w-[420px] flex-col border-l border-hair bg-surface shadow-2xl"
    >
      <header className="flex items-center justify-between border-b border-hair px-4 py-3">
        <div>
          <div className="text-[14px] font-semibold">Ask the plan</div>
          <div className="text-[12px] text-ink-3">{context}</div>
        </div>
        <div className="flex gap-1">
          {msgs.length > 0 && (
            <button onClick={() => setMsgs([])} className="rounded px-2 py-1 text-[12px] text-ink-2 hover:bg-surface-2">
              Clear
            </button>
          )}
          <button onClick={() => setOpen(false)} aria-label="Close" className="rounded px-2 py-1 text-[16px] leading-none text-ink-2 hover:bg-surface-2">
            ×
          </button>
        </div>
      </header>
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {msgs.length === 0 && (
          <div className="grid gap-3">
            <p className="text-[13px] text-ink-2">
              Ask about this plan, or type a what-if. What-ifs are re-solved by the optimizer; answers only use numbers from the
              plan, and every number is checked.
            </p>
            <div className="grid gap-1.5">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)} className="rounded-lg border border-hair px-3 py-2 text-left text-[13px] text-ink-2 hover:bg-surface-2 hover:text-ink">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="grid gap-3">
          {msgs.map((m, i) => (
            <div key={i} className="grid gap-1">
              {m.context && <div className="my-1 text-center text-[11px] text-ink-3">— {m.context} —</div>}
              <div
                className={`max-w-[92%] whitespace-pre-wrap rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed ${
                  m.role === "user" ? "ml-auto rounded-br-md bg-ink text-[color:var(--surface)]" : "rounded-bl-md bg-surface-2"
                }`}
              >
                {m.content}
              </div>
              {m.role === "assistant" && (m.source || m.scenario) && (
                <div className="flex flex-wrap items-center gap-2 pl-1 text-[11px] text-ink-3">
                  {m.source && <span>{SOURCE[m.source]}{m.source === "llm" && m.model ? ` · ${m.model.split("@")[0]}` : ""}</span>}
                  {m.note && <span>· {m.note}</span>}
                  {m.scenario && (
                    <button onClick={() => setScenario(m.scenario!.run_id)} className="rounded border border-hair px-1.5 py-0.5 text-[11.5px] text-ink hover:bg-surface">
                      Open scenario in all screens →
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
          {busy && <div className="w-fit rounded-2xl rounded-bl-md bg-surface-2 px-3.5 py-2.5 text-[13px] text-ink-3">Thinking…</div>}
          <div ref={end} />
        </div>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
        className="flex gap-2 border-t border-hair p-3"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={runId ? "Ask a question or type a what-if…" : "Pick a world first"}
          disabled={!runId}
          className="flex-1 rounded-lg border border-hair bg-surface px-3 py-2 text-[13.5px]"
          aria-label="Message"
        />
        <button type="submit" disabled={busy || !input.trim() || !runId} className="rounded-lg bg-ink px-3.5 py-2 text-[13px] font-medium text-[color:var(--surface)] disabled:opacity-40">
          Send
        </button>
      </form>
    </aside>
  );
}
