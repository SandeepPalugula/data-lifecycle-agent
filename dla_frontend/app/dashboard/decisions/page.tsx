"use client";

import { useEffect, useState } from "react";
import { decisionsApi } from "@/lib/api";
import { CheckCircle, XCircle, RefreshCw, TrendingUp, Info } from "lucide-react";

// ── Shared tooltip component ──────────────────────────────────
function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  return (
    <span className="group relative inline-flex items-center gap-1 cursor-default">
      {children}
      <span className="pointer-events-none absolute bottom-full left-0 mb-2 w-56 rounded-lg bg-gray-900 px-3 py-2 text-xs text-white opacity-0 group-hover:opacity-100 transition-opacity duration-150 z-50 shadow-lg leading-relaxed whitespace-normal">
        {text}
        <span className="absolute top-full left-4 border-4 border-transparent border-t-gray-900" />
      </span>
    </span>
  );
}

function TooltipLabel({ label, tip }: { label: string; tip: string }) {
  return (
    <Tooltip text={tip}>
      <span className="text-xs text-gray-400">{label}</span>
      <Info className="w-3 h-3 text-gray-300 shrink-0" />
    </Tooltip>
  );
}

const VERDICT_COLORS: Record<string, string> = {
  delete:    "bg-red-100 text-red-700",
  compress:  "bg-amber-100 text-amber-700",
  keep:      "bg-green-100 text-green-700",
  standdown: "bg-gray-100 text-gray-600",
};

const VERDICT_TIPS: Record<string, string> = {
  delete:    "The agent recommends permanently deleting this conversation. Requires human confirmation before execution.",
  compress:  "The agent recommends compressing this conversation to reduce storage cost. Original is preserved for 48 hours after confirmation.",
  keep:      "The agent recommends keeping this conversation as-is. No action needed.",
  standdown: "The agent stood down — the batch gate determined that acting on this conversation would cost more than it saves.",
};

const STRATEGY_LABELS: Record<string, { label: string; description: string }> = {
  summary:   { label: "Summary",   description: "Brief summary only — content is similar to other conversations, so full reconstruction is not needed." },
  keypoints: { label: "Keypoints", description: "Extract key points only — content is rare but currently low utility. Preserves what makes it unique." },
  qa:        { label: "Q&A",       description: "Structured Q&A format — content is both valuable and unique. Maximum information retention." },
};

function confidenceLabel(score: number): { label: string; color: string } {
  if (score >= 0.75) return { label: "High confidence",   color: "bg-green-50 text-green-700 border-green-200" };
  if (score >= 0.45) return { label: "Medium confidence", color: "bg-yellow-50 text-yellow-700 border-yellow-200" };
  return                     { label: "Low confidence",   color: "bg-red-50 text-red-600 border-red-200" };
}

const CONFIDENCE_TIP =
  "How certain the agent is about this verdict. Combines score quality (API vs fallback), " +
  "signal strength (how far uniqueness/utility are from the neutral midpoint), " +
  "and economic clarity (how decisively the net saving favours action or inaction).";

function fmt(val: number, decimals = 5): string {
  return `$${val.toFixed(decimals)}`;
}

function formatSize(bytes: number): string {
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  return `${(bytes / 1_024).toFixed(1)} KB`;
}

function ForecastSection({ forecast }: { forecast: any }) {
  if (!forecast || forecast.verdict === "keep" || forecast.verdict === "standdown") return null;

  return (
    <div className="mt-4 border-t border-gray-100 pt-4">
      <div className="flex items-center gap-2 mb-3">
        <TrendingUp className="w-4 h-4 text-indigo-500" />
        <Tooltip text="Projected cumulative storage savings if this verdict is confirmed and executed. Does not include one-time agent or recompute costs.">
          <span className="text-xs font-medium text-gray-700">Projected savings if actioned</span>
          <Info className="w-3 h-3 text-gray-300" />
        </Tooltip>
      </div>

      <div className="grid grid-cols-4 gap-3">
        {[
          { label: "Monthly",   value: forecast.monthly_saving_usd, decimals: 5, tip: "Estimated storage cost saved per month after compression or deletion." },
          { label: "3 months",  value: forecast.forecast_3m_usd,   decimals: 4, tip: "Cumulative storage saving over 3 months if actioned now." },
          { label: "6 months",  value: forecast.forecast_6m_usd,   decimals: 4, tip: "Cumulative storage saving over 6 months if actioned now." },
          { label: "12 months", value: forecast.forecast_12m_usd,  decimals: 4, tip: "Cumulative storage saving over 12 months if actioned now." },
        ].map(({ label, value, decimals, tip }) => (
          <div key={label} className="bg-indigo-50 rounded-lg p-2.5">
            <TooltipLabel label={label} tip={tip} />
            <p className="text-sm font-semibold text-indigo-700 mt-0.5">{fmt(value, decimals)}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-4 mt-3 flex-wrap">
        <span className="text-xs text-gray-500 flex items-center gap-1">
          <Tooltip text="How many months of storage savings are needed to recover the one-time agent scoring cost. Lower is better.">
            <span>Break-even:</span>
            <Info className="w-3 h-3 text-gray-300" />
          </Tooltip>{" "}
          <span className="font-medium text-gray-700">
            {forecast.break_even_months === 9999 ? "never"
              : forecast.break_even_months < 0.1 ? "< 0.1 months"
              : `${forecast.break_even_months.toFixed(1)} months`}
          </span>
        </span>
        {forecast.verdict === "compress" && (
          <span className="text-xs text-gray-500 flex items-center gap-1">
            <Tooltip text="The target size of the compressed conversation relative to its original. 30% means the compressed version is ~30% of the original, eliminating 70% of its storage footprint.">
              <span>Compression ratio:</span>
              <Info className="w-3 h-3 text-gray-300" />
            </Tooltip>{" "}
            <span className="font-medium text-gray-700">
              {Math.round(forecast.compression_ratio * 100)}% of original
            </span>
          </span>
        )}
      </div>

      {forecast.note && <p className="text-xs text-gray-400 mt-2 italic">{forecast.note}</p>}
    </div>
  );
}

export default function DecisionsPage() {
  const [decisions, setDecisions]     = useState<any[]>([]);
  const [pendingOnly, setPendingOnly] = useState(false);
  const [loading, setLoading]         = useState(true);
  const [actioning, setActioning]     = useState<string | null>(null);
  const [mounted, setMounted]         = useState(false);

  useEffect(() => { setMounted(true); }, []);

  const formatDate = (ts: string | null | undefined): string => {
    if (!mounted || !ts) return "—";
    try {
      const d = new Date(ts);
      if (isNaN(d.getTime())) return ts;
      return d.toLocaleString();
    } catch { return ts; }
  };

  const fetchDecisions = async () => {
    setLoading(true);
    try {
      const res = await decisionsApi.list(pendingOnly);
      setDecisions(Array.isArray(res.data) ? res.data : []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchDecisions(); }, [pendingOnly]);

  const handleAction = async (id: string, action: "confirm" | "reject") => {
    setActioning(id);
    try {
      if (action === "confirm") await decisionsApi.confirm(id);
      else await decisionsApi.reject(id);
      await fetchDecisions();
    } catch (e) { console.error(e); }
    finally { setActioning(null); }
  };

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Decisions</h1>
          <p className="text-gray-500 text-sm mt-1">Review and action agent verdicts</p>
        </div>
        <div className="flex gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
            <input type="checkbox" checked={pendingOnly}
              onChange={(e) => setPendingOnly(e.target.checked)} className="rounded" />
            Pending only
          </label>
          <button onClick={fetchDecisions}
            className="flex items-center gap-2 px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white hover:bg-gray-50">
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center text-gray-400 text-sm py-12">Loading...</div>
      ) : decisions.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <CheckCircle className="w-10 h-10 text-green-500 mx-auto mb-3" />
          <p className="text-gray-600 font-medium">No decisions found</p>
          <p className="text-gray-400 text-sm mt-1">Trigger a scheduler run to generate verdicts.</p>
        </div>
      ) : (
        <div className="space-y-4">
          {decisions.map((d) => (
            <div key={d.id} className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    <Tooltip text="The external identifier of this conversation in your storage system.">
                      <span className="text-sm font-semibold text-gray-800 font-mono">{d.conversation_external_id}</span>
                    </Tooltip>
                    <span className="text-xs text-gray-400">·</span>
                    <Tooltip text="Total size of the conversation in storage.">
                      <span className="text-xs text-gray-500">{formatSize(d.conversation_size_bytes)}</span>
                    </Tooltip>
                    <span className="text-xs text-gray-400">·</span>
                    <Tooltip text="How long ago this conversation was created. Older conversations with zero access are stronger candidates for action.">
                      <span className="text-xs text-gray-500">{d.conversation_age_days} days old</span>
                    </Tooltip>
                  </div>

                  <div className="flex items-center gap-3 mb-2 flex-wrap">
                    <Tooltip text={VERDICT_TIPS[d.verdict] || "The agent's recommended action for this conversation."}>
                      <span className={`px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase ${VERDICT_COLORS[d.verdict] || "bg-gray-100 text-gray-600"}`}>
                        {d.verdict}
                      </span>
                    </Tooltip>

                    {d.verdict === "compress" && d.compression_strategy && (() => {
                      const s = STRATEGY_LABELS[d.compression_strategy];
                      return s ? (
                        <Tooltip text={s.description}>
                          <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-600 border border-amber-200">
                            Strategy: {s.label}
                          </span>
                        </Tooltip>
                      ) : null;
                    })()}

                    {d.confidence_score != null && (() => {
                      const { label, color } = confidenceLabel(d.confidence_score);
                      return (
                        <Tooltip text={CONFIDENCE_TIP}>
                          <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium border ${color}`}>
                            {label} · {(d.confidence_score * 100).toFixed(0)}%
                          </span>
                        </Tooltip>
                      );
                    })()}

                    {d.confirmed_at && <span className="text-xs text-green-600 font-medium">Confirmed</span>}
                    {d.rejected_at  && <span className="text-xs text-red-500 font-medium">Rejected</span>}
                  </div>

                  {d.reasoning && <p className="text-sm text-gray-600 mb-3 leading-relaxed">{d.reasoning}</p>}

                  <div className="grid grid-cols-3 gap-3">
                    <div className="bg-gray-50 rounded-lg p-2.5">
                      <TooltipLabel label="Storage saving" tip="Estimated monthly storage cost eliminated if this verdict is executed. Based on conversation size and current storage pricing." />
                      <p className="text-sm font-semibold text-green-600 mt-0.5">{fmt(parseFloat(d.storage_saving_usd))}</p>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-2.5">
                      <TooltipLabel label="Agent cost" tip="What it cost to run the AI scoring for this conversation. Proportional to token count and shared across cluster members." />
                      <p className="text-sm font-semibold text-gray-500 mt-0.5">{fmt(parseFloat(d.agent_cost_usd))}</p>
                    </div>
                    <div className="bg-gray-50 rounded-lg p-2.5">
                      <TooltipLabel label="Net saving" tip="Storage saving minus agent cost and estimated recompute cost. Positive means the action is economically justified. Negative means it costs more to act than to keep." />
                      <p className={`text-sm font-semibold mt-0.5 ${parseFloat(d.net_saving_usd) >= 0 ? "text-green-600" : "text-red-500"}`}>
                        {fmt(parseFloat(d.net_saving_usd))}
                      </p>
                    </div>
                  </div>

                  {(d.uniqueness_score != null || d.utility_score != null) && (
                    <div className="flex gap-4 mt-3">
                      {d.uniqueness_score != null && (
                        <Tooltip text="How rare or irreplaceable this conversation's content is (0–100%). High uniqueness means the content is hard to recreate — favour keeping or compressing over deletion.">
                          <span className="text-xs text-gray-500">
                            Uniqueness: <span className="font-medium text-gray-700">{(d.uniqueness_score * 100).toFixed(0)}%</span>
                          </span>
                          <Info className="w-3 h-3 text-gray-300" />
                        </Tooltip>
                      )}
                      {d.utility_score != null && (
                        <Tooltip text="How likely this conversation is to be useful to the user in the future (0–100%). Low utility combined with low access count is a strong signal for compression or deletion.">
                          <span className="text-xs text-gray-500">
                            Utility: <span className="font-medium text-gray-700">{(d.utility_score * 100).toFixed(0)}%</span>
                          </span>
                          <Info className="w-3 h-3 text-gray-300" />
                        </Tooltip>
                      )}
                    </div>
                  )}

                  <ForecastSection forecast={d.forecast} />
                </div>

                {d.confirmation_required && !d.confirmed_at && !d.rejected_at && (
                  <div className="flex flex-col gap-2 shrink-0">
                    <button onClick={() => handleAction(d.id, "confirm")} disabled={actioning === d.id}
                      className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 disabled:opacity-50">
                      <CheckCircle className="w-4 h-4" />
                      {d.verdict === "delete" ? "Confirm delete" : "Confirm"}
                    </button>
                    <button onClick={() => handleAction(d.id, "reject")} disabled={actioning === d.id}
                      className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 disabled:opacity-50">
                      <XCircle className="w-4 h-4" /> Reject
                    </button>
                  </div>
                )}
              </div>

              <p className="text-xs text-gray-400 mt-3">
                ID: {d.id} · Created {formatDate(d.created_at)}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
