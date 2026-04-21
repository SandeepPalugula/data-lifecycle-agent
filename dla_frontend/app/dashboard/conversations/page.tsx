/**
 * app/dashboard/conversations/page.tsx
 */

"use client";

import { useEffect, useState, useRef } from "react";
import { conversationsApi } from "@/lib/api";
import { Shield, RefreshCw, Info } from "lucide-react";

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

function ThTip({ label, tip }: { label: string; tip: string }) {
  return (
    <Tooltip text={tip}>
      <span>{label}</span>
      <Info className="w-3 h-3 text-gray-400 shrink-0" />
    </Tooltip>
  );
}

// ── Constants ─────────────────────────────────────────────────
const STATE_COLORS: Record<string, string> = {
  active:           "bg-green-100 text-green-700",
  compressed:       "bg-blue-100 text-blue-700",
  deleted:          "bg-red-100 text-red-700",
  safety_locked:    "bg-orange-100 text-orange-700",
  pending_analysis: "bg-yellow-100 text-yellow-700",
};

const STATE_TIPS: Record<string, string> = {
  active:           "This conversation is being tracked and is eligible for analysis in the next scheduler run.",
  compressed:       "This conversation has been compressed. The original content was replaced with a compressed version to save storage.",
  deleted:          "This conversation has been permanently deleted from the system.",
  safety_locked:    "This conversation has been flagged for safety review and is excluded from all automated analysis until cleared.",
  pending_analysis: "This conversation is queued for analysis in an upcoming scheduler run.",
};

const COLUMN_TIPS: Record<string, string> = {
  "External ID": "The identifier used in your external conversation storage system (e.g. S3 key or database ID).",
  "Size":        "Total storage footprint of this conversation including all messages. Used to calculate potential storage savings.",
  "Accesses":    "How many times this conversation has been accessed. Zero accesses over a long period is a key signal for compression or deletion candidates.",
  "State":       "The current lifecycle state of this conversation.",
  "Uniqueness":  "How rare or irreplaceable this conversation's content is (0–100%). Scored by the AI agent. High values favour keeping over deleting.",
  "Utility":     "How likely this conversation is to be useful to the user in the future (0–100%). Scored by the AI agent. Low values are a signal for action.",
  "Flagged":     "Whether this conversation has an active safety flag. Flagged conversations are excluded from automated analysis.",
};

export default function ConversationsPage() {
  const [data, setData]               = useState<any>(null);
  const [page, setPage]               = useState(1);
  const [stateFilter, setStateFilter] = useState("");
  const [loading, setLoading]         = useState(true);

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await conversationsApi.list(page, 20, stateFilter || undefined);
      setData(res.data);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchData(); }, [page, stateFilter]);

  const formatBytes = (b: number) => {
    if (b < 1024) return `${b} B`;
    if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
    return `${(b / 1024 / 1024).toFixed(1)} MB`;
  };

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Conversations</h1>
          <p className="text-gray-500 text-sm mt-1">{data?.total || 0} conversations tracked</p>
        </div>
        <div className="flex gap-3">
          <select
            value={stateFilter}
            onChange={(e) => { setStateFilter(e.target.value); setPage(1); }}
            className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white"
          >
            <option value="">All states</option>
            <option value="active">Active</option>
            <option value="compressed">Compressed</option>
            <option value="deleted">Deleted</option>
            <option value="safety_locked">Safety locked</option>
            <option value="pending_analysis">Pending analysis</option>
          </select>
          <button
            onClick={fetchData}
            className="flex items-center gap-2 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      </div>

      {/* overflow-visible so tooltips on bottom rows are not clipped by the container */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-visible">
        {loading ? (
          <div className="p-8 text-center text-gray-400 text-sm">Loading...</div>
        ) : !data?.items?.length ? (
          <div className="p-8 text-center text-gray-400 text-sm">
            No conversations yet. Register conversations via the API to get started.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {Object.entries(COLUMN_TIPS).map(([h, tip]) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">
                    <ThTip label={h} tip={tip} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {data.items.map((conv: any) => (
                <tr key={conv.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-xs text-gray-700 max-w-xs truncate">
                    {conv.external_id}
                  </td>
                  <td className="px-4 py-3 text-gray-600">{formatBytes(conv.size_bytes)}</td>
                  <td className="px-4 py-3 text-gray-600">{conv.access_count}</td>
                  <td className="px-4 py-3">
                    <Tooltip text={STATE_TIPS[conv.state] || conv.state}>
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATE_COLORS[conv.state] || "bg-gray-100 text-gray-600"}`}>
                        {conv.state}
                      </span>
                    </Tooltip>
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {conv.uniqueness_score != null ? `${(conv.uniqueness_score * 100).toFixed(0)}%` : "—"}
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {conv.utility_score != null ? `${(conv.utility_score * 100).toFixed(0)}%` : "—"}
                  </td>
                  <td className="px-4 py-3">
                    {conv.is_flagged && (
                      <Tooltip text="This conversation has an active safety flag and is excluded from automated analysis.">
                        <Shield className="w-4 h-4 text-orange-500" />
                      </Tooltip>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {data?.total > 20 && (
          <div className="px-4 py-3 border-t border-gray-200 flex items-center justify-between">
            <p className="text-xs text-gray-500">Page {page} of {Math.ceil(data.total / 20)}</p>
            <div className="flex gap-2">
              <button disabled={page === 1} onClick={() => setPage(p => p - 1)}
                className="px-3 py-1 border rounded text-xs disabled:opacity-40 hover:bg-gray-50">Previous</button>
              <button disabled={page >= Math.ceil(data.total / 20)} onClick={() => setPage(p => p + 1)}
                className="px-3 py-1 border rounded text-xs disabled:opacity-40 hover:bg-gray-50">Next</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
