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
