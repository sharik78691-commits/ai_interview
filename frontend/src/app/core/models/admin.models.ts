/** Admin-panel models (mirror backend app/schemas/admin.py). */

export interface DayCount {
  day: string;
  count: number;
}

export interface AdminUserRow {
  id: number;
  email: string;
  name: string;
  username: string | null;
  provider: string;
  is_admin: boolean;
  is_active: boolean;
  login_count: number;
  last_login_at: string | null;
  created_at: string | null;
}

export interface AdminUsersPage {
  total: number;
  page: number;
  page_size: number;
  users: AdminUserRow[];
}

export interface SystemStatus {
  demo_mode: boolean;
  llm_configured: boolean;
  stt_configured: boolean;
  google_configured: boolean;
  database: string;
  version: string;
}

export interface Totals {
  users_total: number;
  active_users: number;
  admin_count: number;
  total_logins: number;
}

export interface RangeInfo {
  from_date: string;
  to_date: string;
}

export interface InRange {
  signups: number;
  interviews: number;
  questions: number;
  ai_calls: number;
  ai_errors: number;
}

export interface AiUsageRow {
  provider: string;
  model: string;
  calls: number;
  errors: number;
  avg_latency_ms: number | null;
}

export interface AdminStats {
  system: SystemStatus;
  range: RangeInfo;
  totals: Totals;
  in_range: InRange;
  signups_by_day: DayCount[];
  ai_usage: AiUsageRow[];
  recent_users: AdminUserRow[];
}

/** Speech-to-text model (for the admin STT management section). */
export interface SttModel {
  key: string;
  provider: string;
  displayName: string;
  modelId: string;
  available: boolean;
  enabled: boolean;
  isDefault: boolean;
}

export interface SttModelsResponse {
  models: SttModel[];
  default: string;
  active: string | null;
}

// ------------------------------------------------------------ business report
// Mirrors backend app/services/business_analytics.py (GET /api/admin/business).

export interface Kpi {
  value: number;
  previous: number;
  delta_pct: number | null;
}

export interface FunnelStep {
  key: string;
  label: string;
  count: number;
  pct_of_signups: number | null;
}

export interface SegmentRow {
  key: string;
  label: string;
  count: number;
  hint: string;
}

export interface DailyPoint {
  day: string;
  signups: number;
  active: number;
  questions: number;
}

export interface PersonRow {
  id: number;
  name: string;
  email: string;
  provider: string;
  questions: number;
  questions_total: number;
  interviews: number;
  active_days: number;
  last_active: string | null;
  days_inactive: number | null;
  signed_up: string | null;
}

export interface Insight {
  level: 'action' | 'warn' | 'good';
  title: string;
  detail: string;
  action: string;
}

export interface BusinessReport {
  range: { from_date: string; to_date: string; days: number; previous_from: string; previous_to: string };
  kpis: { new_users: Kpi; active_users: Kpi; interviews: Kpi; questions: Kpi };
  engagement: {
    dau: number;
    wau: number;
    mau: number;
    stickiness_pct: number | null;
    questions_per_active_user: number | null;
  };
  funnel: FunnelStep[];
  activation_rate_pct: number | null;
  retention_rate_pct: number | null;
  segments: SegmentRow[];
  daily: DailyPoint[];
  heatmap: number[][];
  auth_mix: { provider: string; count: number; pct: number | null }[];
  ai: {
    calls: number;
    errors: number;
    success_rate_pct: number | null;
    p50_latency_ms: number | null;
    p95_latency_ms: number | null;
    input_tokens: number;
    output_tokens: number;
    calls_per_active_user: number | null;
  };
  top_users: PersonRow[];
  win_back: PersonRow[];
  never_started: PersonRow[];
  totals: { members: number; activated: number; never_activated: number; lapsed_total: number };
  insights: Insight[];
}

// ------------------------------------------------------------- AI cost report
// Mirrors backend app/services/ai_cost.py (GET /api/admin/ai-costs).

export interface AiCostModelRow {
  key: string;
  display_name: string;
  provider: string;
  model_id: string;
  free_tier: boolean;
  calls: number;
  ok: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
  estimated_calls: number;
  cost_usd: number;
  cost_inr: number;
  billed_inr: number;
  cost_per_call_inr: number | null;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  success_rate_pct: number | null;
  unique_users: number;
}

export interface AiCostWindow {
  label: string;
  days: number;
  totals: {
    calls: number;
    ok: number;
    errors: number;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    cost_inr: number;
    billed_inr: number;
    cost_per_call_inr: number | null;
    estimated_share_pct: number | null;
    unique_users: number;
  };
  models: AiCostModelRow[];
}

export interface AiCostBucket {
  label: string;
  calls: number;
  cost_inr: number;
  by_model: Record<string, { calls: number; cost_inr: number }>;
}

export type AiCostWindowKey = '24h' | '7d' | '30d' | '90d';

export interface AiCostReport {
  generated_at: string;
  usd_to_inr: number;
  token_coverage_pct: number | null;
  pricing: {
    key: string;
    display_name: string;
    input_per_m_usd: number;
    output_per_m_usd: number;
    free_tier: boolean;
    known_price: boolean;
  }[];
  windows: Record<AiCostWindowKey, AiCostWindow>;
  series: Record<AiCostWindowKey, AiCostBucket[]>;
  run_rate: { monthly_cost_inr: number; monthly_billed_inr: number; based_on: string };
}
