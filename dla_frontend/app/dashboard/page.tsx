/**
 * app/dashboard/page.tsx
 * Overview page — the first thing you see after login.
 */

"use client";

import { useEffect, useState } from "react";
import { schedulerApi, decisionsApi, costsApi, conversationsApi } from "@/lib/api";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, ResponsiveContainer
} from "recharts";
import { DollarSign, MessageSquare, AlertCircle, TrendingUp, Info } from "lucide-react";

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
      <span className="text-xs text-gray-500">{label}</span>
      <Info className="w-3 h-3 text-gray-300 shrink-0" />
    </Tooltip>
  );
}

// ── Types ─────────────────────────────────────────────────────
interface MetricCard {
  label: string;
  tip:   string;
  value: string;
  icon:  React.ElementType;
  color: string;
}

export default function OverviewPage() {
  const [runs, setRuns]                   = useState<any[]>([]);
  const [pending, setPending]             = useState(0);
  const [totalConvs, setTotalConvs]       = useState(0);
  const [costs, setCosts]                 = useState<any>(null);
  const [batchForecast, setBatchForecast] = useState<any>(null);
  const [loading, setLoading]             = useState(true);

  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [runsRes, decisionsRes, convsRes, costsRes, forecastRes] = await Promise.all([
          schedulerApi.listRuns(),
          decisionsApi.list(true),
          conversationsApi.list(1, 1),
          costsApi.latest(),
          decisionsApi.getBatchForecast(),
        ]);
        setRuns(runsRes.data.slice(0, 10));
        setPending(Array.isArray(decisionsRes.data) ? decisionsRes.data.length : 0);
        setTotalConvs(convsRes.data.total || 0);
        setCosts(costsRes.data);
        setBatchForecast(forecastRes.data);
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    fetchAll();
  }, []);

  const totalSaved     = runs.reduce((sum, r) => sum + (r.net_saving_usd  || 0), 0);
  const totalProcessed = runs.reduce((sum, r) => sum + (r.jobs_processed  || 0), 0);

  const metrics: MetricCard[] = [
    {
      label: "Total net savings",
      tip:   "Sum of net savings across the last 10 scheduler runs. Net saving = storage saving minus agent scoring cost and estimated recompute cost.",
      value: `$${totalSaved.toFixed(4)}`,
      icon:  DollarSign,
      color: "text-green-600 bg-green-50",
    },
    {
      label: "Conversations tracked",
      tip:   "Total number of conversations registered in the system across all lifecycle states (active, compressed, deleted, etc.).",
      value: totalConvs.toString(),
      icon:  MessageSquare,
      color: "text-indigo-600 bg-indigo-50",
    },
    {
      label: "Pending decisions",
      tip:   "Compress or delete verdicts that have been issued but are awaiting human confirmation. These expire after 24 hours if not actioned.",
      value: pending.toString(),
      icon:  AlertCircle,
      color: "text-amber-600 bg-amber-50",
    },
    {
      label: "Jobs processed",
      tip:   "Total conversations analysed across the last 10 scheduler runs, including those kept via heuristic pre-screen.",
      value: totalProcessed.toString(),
      icon:  TrendingUp,
      color: "text-blue-600 bg-blue-50",
    },
  ];

  const chartData = runs
    .slice()
    .reverse()
    .map((r, i) => ({
      name:   `Run ${i + 1}`,
      saving: parseFloat((r.net_saving_usd || 0).toFixed(4)),
      cost:   parseFloat((r.agent_cost_usd || 0).toFixed(4)),
    }));

  if (loading) {
    return (
      <div className="p-8">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-gray-200 rounded w-48"></div>
          <div className="grid grid-cols-4 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-24 bg-gray-200 rounded-xl"></div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-semibold text-gray-900">Overview</h1>
        <p className="text-gray-500 text-sm mt-1">Agent activity summary and cost performance</p>
      </div>

      {/* Metric cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {metrics.map(({ label, tip, value, icon: Icon, color }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-200 p-4">
            <div className={`w-9 h-9 rounded-lg ${color} flex items-center justify-center mb-3`}>
              <Icon className="w-5 h-5" />
            </div>
            <p className="text-2xl font-semibold text-gray-900">{value}</p>
            <div className="mt-1">
              <Tooltip text={tip}>
                <span className="text-xs text-gray-500">{label}</span>
                <Info className="w-3 h-3 text-gray-300" />
              </Tooltip>
            </div>
          </div>
        ))}
      </div>

      {/* Batch forecast card */}
      {batchForecast && batchForecast.actionable_count > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-6 mb-6">
          <div className="flex items-center gap-2 mb-1">
            <TrendingUp className="w-4 h-4 text-indigo-500" />
            <Tooltip text="Total projected storage savings if every pending compress and delete verdict is confirmed and executed today.">
              <h2 className="text-sm font-semibold text-gray-900">
                Projected savings if all pending decisions are actioned
              </h2>
              <Info className="w-3 h-3 text-gray-300" />
            </Tooltip>
          </div>
          <p className="text-xs text-gray-400 mb-5">
            Based on {batchForecast.actionable_count} pending compress / delete verdict
            {batchForecast.actionable_count !== 1 ? "s" : ""}
          </p>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
            {[
              { label: "Monthly",   value: batchForecast.total_monthly_usd, decimals: 5, tip: "Estimated storage cost saved per month if all pending verdicts are actioned." },
              { label: "3 months",  value: batchForecast.forecast_3m_usd,   decimals: 4, tip: "Cumulative storage saving over 3 months if all pending verdicts are actioned now." },
              { label: "6 months",  value: batchForecast.forecast_6m_usd,   decimals: 4, tip: "Cumulative storage saving over 6 months if all pending verdicts are actioned now." },
              { label: "12 months", value: batchForecast.forecast_12m_usd,  decimals: 4, tip: "Cumulative storage saving over 12 months if all pending verdicts are actioned now." },
            ].map(({ label, value, decimals, tip }) => (
              <div key={label} className="bg-indigo-50 rounded-lg p-3">
                <Tooltip text={tip}>
                  <p className="text-xs text-indigo-400">{label}</p>
                  <Info className="w-3 h-3 text-indigo-300" />
                </Tooltip>
                <p className="text-lg font-semibold text-indigo-700 mt-0.5">
                  ${value.toFixed(decimals)}
                </p>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-6 text-xs text-gray-500 border-t border-gray-100 pt-4 flex-wrap">
            <span className="flex items-center gap-1">
              <Tooltip text="The total one-time cost of running the AI scoring that produced these verdicts. Already paid — shown for reference.">
                <span>Agent cost to action:</span>
                <Info className="w-3 h-3 text-gray-300" />
              </Tooltip>{" "}
              <span className="font-medium text-gray-700">
                ${batchForecast.total_agent_cost_usd.toFixed(5)}
              </span>
            </span>
            <span className="flex items-center gap-1">
              <Tooltip text="How many months of combined storage savings are needed to recover the total agent cost across all pending verdicts.">
                <span>Break-even:</span>
                <Info className="w-3 h-3 text-gray-300" />
              </Tooltip>{" "}
              <span className="font-medium text-gray-700">
                {batchForecast.break_even_months === 9999
                  ? "never"
                  : batchForecast.break_even_months < 0.1
                  ? "< 0.1 months"
                  : `${batchForecast.break_even_months.toFixed(1)} months`}
              </span>
            </span>
          </div>
        </div>
      )}

      {/* Chart */}
      <div className="bg-white rounded-xl border border-gray-200 p-6 mb-6">
        <Tooltip text="Net saving = storage saving minus agent cost for that run. Negative values mean the agent spent more scoring conversations than it identified in potential savings — usually caused by a batch standdown or test data with low storage cost.">
          <h2 className="text-sm font-semibold text-gray-900 mb-1">
            Net savings vs agent cost per run
          </h2>
          <Info className="w-3 h-3 text-gray-300 mb-1" />
        </Tooltip>
        <p className="text-xs text-gray-400 mb-4">
          Green = net saving · Blue = agent operational cost
        </p>
        {chartData.length === 0 ? (
          <div className="h-48 flex items-center justify-center text-gray-400 text-sm">
            No scheduler runs yet. Trigger a run to see data here.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} />
              <RechartsTooltip formatter={(v: number) => `$${v.toFixed(5)}`} />
              <Bar dataKey="saving" fill="#16a34a" radius={[4, 4, 0, 0]} name="Net saving" />
              <Bar dataKey="cost"   fill="#4f46e5" radius={[4, 4, 0, 0]} name="Agent cost" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Current costs */}
      {costs && (
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h2 className="text-sm font-semibold text-gray-900 mb-4">Current pricing snapshot</h2>
          <div className="grid grid-cols-3 gap-4">
            {[
              {
                label: "Storage cost / GB / day",
                tip:   "The current cost of storing 1 GB of conversation data for one day. Used to calculate storage savings from compression or deletion.",
                value: `$${parseFloat(costs.storage_cost_per_gb_day).toFixed(6)}`,
              },
              {
                label: "Compute cost / 1K tokens",
                tip:   "The current cost of processing 1,000 tokens through the AI scoring model. Used to calculate agent cost and recompute cost estimates.",
                value: `$${parseFloat(costs.compute_cost_per_ktok).toFixed(4)}`,
              },
              {
                label: "Peak pricing factor",
                tip:   "A multiplier applied during peak usage periods. 1.0× means standard pricing. Values above 1.0× indicate elevated costs due to high demand.",
                value: `${parseFloat(costs.peak_factor).toFixed(1)}×`,
              },
            ].map(({ label, tip, value }) => (
              <div key={label} className="bg-gray-50 rounded-lg p-3">
                <TooltipLabel label={label} tip={tip} />
                <p className="text-lg font-semibold text-gray-900 mt-1">{value}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
