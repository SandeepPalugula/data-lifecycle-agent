"use client";

import { useEffect, useState, useRef } from "react";
import { schedulerApi } from "@/lib/api";
import { Play, RefreshCw, CheckCircle, XCircle, Clock, Info } from "lucide-react";

// ── Smart tooltip — flips up or down based on viewport position ──
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const [visible, setVisible]   = useState(false);
  const [flipped, setFlipped]   = useState(false);
  const ref                     = useRef<HTMLSpanElement>(null);

  const handleMouseEnter = () => {
    if (ref.current) {
      const rect = ref.current.getBoundingClientRect();
      setFlipped(window.innerHeight - rect.bottom < 200);
    }
    setVisible(true);
  };

  return (
    <span
      ref={ref}
      className="relative inline-flex items-center gap-1 cursor-default"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={() => setVisible(false)}
    >
      {children}
      {visible && (
        <span
          className={`pointer-events-none absolute left-0 w-56 rounded-lg bg-gray-900 px-3 py-2 text-xs text-white z-50 shadow-lg leading-relaxed whitespace-normal
            ${flipped ? "bottom-full mb-2" : "top-full mt-2"}`}
        >
          {text}
          <span className={`absolute left-4 border-4 border-transparent
            ${flipped ? "top-full border-t-gray-900" : "bottom-full border-b-gray-900"}`}
          />
        </span>
      )}
    </span>
  );
}

// ── Constants ─────────────────────────────────────────────────
const STATUS_CONFIG: Record<string, { color: string; icon: React.ElementType }> = {
  completed: { color: "text-green-600 bg-green-50", icon: CheckCircle },
  running:   { color: "text-blue-600 bg-blue-50",   icon: Clock       },
  aborted:   { color: "text-red-600 bg-red-50",     icon: XCircle     },
  standdown: { color: "text-gray-600 bg-gray-100",  icon: XCircle     },
};

const STATUS_TIPS: Record<string, string> = {
  completed: "The run finished successfully. All conversations were processed and verdicts were written.",
  running:   "The run is currently in progress. Refresh to see the latest status.",
  aborted:   "The run encountered an unrecoverable error and was aborted. Check server logs for details.",
  standdown: "The run stood down — the batch gate determined that the combined agent cost exceeded the potential storage savings.",
};

const COLUMN_TIPS: Record<string, string> = {
  "Status":     "The outcome of this scheduler run.",
  "Trigger":    "Whether this run was started manually by a user or automatically by a cron schedule.",
  "Processed":  "Total conversations that entered the pipeline in this run, including those kept via heuristic pre-screen.",
  "Deleted":    "Conversations given a DELETE verdict in this run. Requires human confirmation before execution.",
  "Compressed": "Conversations given a COMPRESS verdict in this run. Requires human confirmation before execution.",
  "Net saving": "Total storage saving minus total agent cost for this run. Negative means the run cost more to operate than it identified in savings.",
  "Agent cost": "Total cost of all AI scoring API calls made during this run.",
  "ROI":        "Return on investment — net saving divided by agent cost. 2× means $2 in savings for every $1 spent. Negative values indicate a loss.",
  "Started":    "When this run began.",
};

export default function SchedulerPage() {
  const [runs, setRuns]             = useState<any[]>([]);
  const [loading, setLoading]       = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [mounted, setMounted]       = useState(false);
  const [message, setMessage]       = useState<{ text: string; type: "success" | "error" } | null>(null);

  useEffect(() => { setMounted(true); }, []);

  // formatDate lives inside the component so it can access mounted state
  const formatDate = (ts: string | null | undefined): string => {
    if (!mounted || !ts) return "—";
    try {
      const d = new Date(ts);
      if (isNaN(d.getTime())) return ts;
      return d.toLocaleString();
    } catch { return ts; }
  };

  const fetchRuns = async () => {
    setLoading(true);
    try {
      const res = await schedulerApi.listRuns();
      setRuns(Array.isArray(res.data) ? res.data : []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchRuns(); }, []);

  const handleTrigger = async () => {
    setTriggering(true); setMessage(null);
    try {
      const res = await schedulerApi.triggerRun();
      setMessage({ text: res.data.message, type: "success" });
      await fetchRuns();
    } catch (e: any) {
      setMessage({ text: e.response?.data?.detail || "Failed to trigger run.", type: "error" });
    } finally { setTriggering(false); }
  };

  const roi = (run: any) => {
    if (!run.agent_cost_usd || run.agent_cost_usd === 0) return "—";
    return `${(run.net_saving_usd / run.agent_cost_usd).toFixed(1)}×`;
  };

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Scheduler</h1>
          <p className="text-gray-500 text-sm mt-1">Trigger batch runs and view history</p>
        </div>
        <div className="flex gap-3">
          <button onClick={fetchRuns}
            className="flex items-center gap-2 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white hover:bg-gray-50">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
          <button onClick={handleTrigger} disabled={triggering}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
            <Play className="w-4 h-4" />
            {triggering ? "Starting..." : "Trigger run"}
          </button>
        </div>
      </div>

      {message && (
        <div className={`mb-4 px-4 py-3 rounded-lg text-sm ${
          message.type === "success"
            ? "bg-green-50 text-green-700 border border-green-200"
            : "bg-red-50 text-red-700 border border-red-200"
        }`}>{message.text}</div>
      )}

      {loading ? (
        <div className="text-center text-gray-400 text-sm py-12">Loading...</div>
      ) : runs.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <Play className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-600 font-medium">No runs yet</p>
          <p className="text-gray-400 text-sm mt-1">Click "Trigger run" to start the first batch.</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-visible">
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
              {runs.map(run => {
                const { color, icon: Icon } = STATUS_CONFIG[run.status] || STATUS_CONFIG.completed;
                return (
                  <tr key={run.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <Tooltip text={STATUS_TIPS[run.status] || run.status}>
                        <span className={`flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full w-fit ${color}`}>
                          <Icon className="w-3 h-3" />{run.status}
                        </span>
                      </Tooltip>
                    </td>
                    <td className="px-4 py-3 text-gray-600 capitalize">{run.triggered_by}</td>
                    <td className="px-4 py-3 text-gray-900 font-medium">{run.jobs_processed}</td>
                    <td className="px-4 py-3 text-red-600">{run.jobs_deleted}</td>
                    <td className="px-4 py-3 text-amber-600">{run.jobs_compressed}</td>
                    <td className="px-4 py-3 text-green-600 font-medium">${parseFloat(run.net_saving_usd).toFixed(4)}</td>
                    <td className="px-4 py-3 text-gray-500">${parseFloat(run.agent_cost_usd).toFixed(5)}</td>
                    <td className="px-4 py-3 text-indigo-600 font-medium">{roi(run)}</td>
                    <td className="px-4 py-3 text-gray-400 text-xs">{formatDate(run.started_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
