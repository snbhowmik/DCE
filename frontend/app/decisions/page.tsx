"use client";
// 07 Decision log: every accept / reject with its reason, tied to the run it was made on (FR-30).
import { Card, PageHeader, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";

export default function DecisionsPage() {
  const { data, error, loading } = useAsync("all", () => api.decisions());
  return (
    <div className="grid gap-5">
      <PageHeader
        title="Decision log"
        lede="Recommendations accepted or rejected, with the reason, the person and the run. On the next data drop these are compared with what actually happened (closed loop, T12.4)."
      />
      <Card>
        {loading && <p className="text-[13px] text-ink-3">Loading…</p>}
        {error && <p className="text-[13px] text-critical">{error}</p>}
        {data && data.length === 0 && (
          <p className="text-[13px] text-ink-2">
            No decisions yet. Accept or reject a co-man activation on <em>Allocation plan</em> or an alert on{" "}
            <em>Alerts &amp; mitigations</em>.
          </p>
        )}
        {data && data.length > 0 && (
          <table className="dtable text-[13px]">
            <thead>
              <tr>
                <th>When</th>
                <th>Decision</th>
                <th>Recommendation</th>
                <th>Reason</th>
                <th>By</th>
                <th>Run</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.id}>
                  <td className="whitespace-nowrap text-ink-2">{new Date(d.ts).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</td>
                  <td>
                    <StatusPill tone={d.action === "accept" ? "good" : "neutral"}>{d.action === "accept" ? "Accepted" : "Rejected"}</StatusPill>
                  </td>
                  <td>
                    <div>{d.summary}</div>
                    <div className="text-[11.5px] text-ink-3">{d.kind}</div>
                  </td>
                  <td className="text-ink-2">{d.reason}</td>
                  <td className="text-ink-2">{d.user}</td>
                  <td className="font-mono text-[11.5px] text-ink-3">{d.run_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
