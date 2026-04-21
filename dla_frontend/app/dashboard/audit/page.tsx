"use client";

import { useEffect, useState, useRef } from "react";
import { auditApi } from "@/lib/api";
import { RefreshCw, ChevronDown, ChevronUp, Info } from "lucide-react";

// ── Smart tooltip — flips up or down based on viewport position ──
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const [visible, setVisible] = useState(false);
  const [flipped, setFlipped] = useState(false);
  const ref                   = useRef<HTMLSpanElement>(null);

  const handleMouseEnter = () => {
    if (ref.current) {
      const rect = ref.current.getBoundingClientRect();
      setFlipped(window.innerHeight - rect.bottom < 200);
    }
    setVisible(true);
  };

  return (
    <span ref={ref} className="relative inline-flex items-center gap-1 cursor-default"
      onMouseEnter={handleMouseEnter} onMouseLeave={() => setVisible(false)}>
      {children}
      {visible && (
        <span className={`pointer-events-none absolute left-0 w-56 rounded-lg bg-gray-900 px-3 py-2 text-xs text-white z-50 shadow-lg leading-relaxed whitespace-normal
          ${flipped ? "bottom-full mb-2" : "top-full mt-2"}`}>
          {text}
          <span className={`absolute left-4 border-4 border-transparent
            ${flipped ? "top-full border-t-gray-900" : "bottom-full border-b-gray-900"}`} />
        </span>
      )}
    </span>
  );
}

const EVENT_COLORS: Record<string, string> = {
  deletion_executed:       "bg-red-100 text-red-700",
  compression_executed:    "bg-amber-100 text-amber-700",
  verdict_issued:          "bg-indigo-100 text-indigo-700",
  confirmation_received:   "bg-green-100 text-green-700",
  standdown:               "bg-gray-100 text-gray-600",
  scheduler_run_started:   "bg-blue-100 text-blue-700",
  scheduler_run_completed: "bg-blue-100 text-blue-700",
  auth_login:              "bg-purple-100 text-purple-700",
  safety_block:            "bg-orange-100 text-orange-700",
  job_started:             "bg-gray-100 text-gray-500",
  job_failed:              "bg-red-100 text-red-600",
};

const EVENT_TIPS: Record<string, string> = {
  deletion_executed:       "A conversation was permanently deleted after a confirmed delete verdict.",
  compression_executed:    "A conversation was compressed, or a compression's rollback window elapsed and the original was permanently committed.",
  verdict_issued:          "The agent issued a keep, compress, delete, or standdown verdict for a conversation.",
  confirmation_received:   "A human confirmed or rejected a pending verdict via the Decisions page.",
  standdown:               "The agent stood down — the batch gate or decision rules determined no action was economically justified.",
  scheduler_run_started:   "A new batch scheduler run was triggered.",
  scheduler_run_completed: "A batch scheduler run completed successfully.",
  auth_login:              "A user logged in to the dashboard.",
  safety_block:            "A conversation was blocked from analysis due to an active safety flag.",
  job_started:             "An analysis job started processing a conversation.",
  job_failed:              "An analysis job failed. The conversation will be requeued by R1 recovery on the next run.",
};

const COLUMN_TIPS: Record<string, string> = {
  "#":           "Sequential audit log entry number. The audit log is append-only and immutable — entries can never be edited or deleted.",
  "Event":       "The type of action recorded. Hover over individual event badges for a description of what each event means.",
  "Actor":       "Who or what triggered this event. 'Agent' means the AI pipeline, 'User' means a logged-in human, 'System' means an automated process.",
  "Conversation":"The first 8 characters of the conversation ID this event relates to, if applicable.",
  "Detail":      "Event-specific metadata. Expand to see all fields including verdict, scores, cost breakdown, and method.",
  "Timestamp":   "When this event was recorded. All timestamps are in your local timezone.",
};

function DetailCell({ detail }: { detail: any }) {
  const [expanded, setExpanded] = useState(false);

  if (!detail || typeof detail !== "object") {
    return <span className="text-gray-400 text-xs">—</span>;
  }

  const entries = Object.entries(detail);
  const preview = entries.slice(0, 2).map(([k, v]) =>
    `${k}: ${typeof v === "number" ? Number(v).toFixed(4) : String(v).slice(0, 30)}`
  ).join(" · ");

  return (
    <div className="text-xs">
      <div className="text-gray-500 font-mono">{preview}</div>
      {entries.length > 2 && (
        <button onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 text-indigo-500 hover:text-indigo-700 mt-1 text-xs">
          {expanded ? <><ChevronUp className="w-3 h-3" /> less</> : <><ChevronDown className="w-3 h-3" /> {entries.length - 2} more fields</>}
        </button>
      )}
      {expanded && (
        <div className="mt-2 bg-gray-50 rounded p-2 space-y-1 border border-gray-100">
          {entries.map(([k, v]) => (
            <div key={k} className="flex gap-2">
              <span className="text-gray-400 font-mono shrink-0">{k}:</span>
              <span className="text-gray-700 font-mono break-all">
                {typeof v === "number" ? Number(v).toFixed(6)
                  : typeof v === "string" ? v
                  : JSON.stringify(v)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function AuditPage() {
  const [data, setData]       = useState<any>(null);
  const [page, setPage]       = useState(1);
  const [loading, setLoading] = useState(true);
  const [mounted, setMounted] = useState(false);

  useEffect(() => { setMounted(true); }, []);

  const formatDate = (ts: string | null | undefined): string => {
    if (!mounted || !ts) return "—";
    try {
      const d = new Date(ts);
      if (isNaN(d.getTime())) return ts;
      return d.toLocaleString();
    } catch { return ts; }
  };

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await auditApi.list(page, 50);
      setData(res.data);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchData(); }, [page]);

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Audit log</h1>
          <p className="text-gray-500 text-sm mt-1">Immutable record of all agent and user actions</p>
        </div>
        <button onClick={fetchData}
          className="flex items-center gap-2 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white hover:bg-gray-50">
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-visible">
        {loading ? (
          <div className="p-8 text-center text-gray-400 text-sm">Loading...</div>
        ) : !data?.items?.length ? (
          <div className="p-8 text-center text-gray-400 text-sm">No audit events yet.</div>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  {Object.entries(COLUMN_TIPS).map(([h, tip]) => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                      <Tooltip text={tip}>
                        <span>{h}</span>
                        <Info className="w-3 h-3 text-gray-400 shrink-0" />
                      </Tooltip>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.items.map((entry: any) => (
                  <tr key={entry.id} className="hover:bg-gray-50 align-top">
                    <td className="px-4 py-3 text-gray-400 text-xs">{entry.id}</td>
                    <td className="px-4 py-3">
                      <Tooltip text={EVENT_TIPS[entry.event_type] || entry.event_type}>
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${EVENT_COLORS[entry.event_type] || "bg-gray-100 text-gray-600"}`}>
                          {entry.event_type.replace(/_/g, " ")}
                        </span>
                      </Tooltip>
                    </td>
                    <td className="px-4 py-3 text-gray-600 capitalize text-xs">{entry.actor_type}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-500">
                      {entry.conversation_id ? entry.conversation_id.slice(0, 8) + "..." : "—"}
                    </td>
                    <td className="px-4 py-3 max-w-sm"><DetailCell detail={entry.detail} /></td>
                    <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">
                      {formatDate(entry.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            {data.total > 50 && (
              <div className="px-4 py-3 border-t border-gray-200 flex items-center justify-between">
                <p className="text-xs text-gray-500">Page {page} of {Math.ceil(data.total / 50)}</p>
                <div className="flex gap-2">
                  <button disabled={page === 1} onClick={() => setPage(p => p - 1)}
                    className="px-3 py-1 border rounded text-xs disabled:opacity-40 hover:bg-gray-50">Previous</button>
                  <button disabled={page >= Math.ceil(data.total / 50)} onClick={() => setPage(p => p + 1)}
                    className="px-3 py-1 border rounded text-xs disabled:opacity-40 hover:bg-gray-50">Next</button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
