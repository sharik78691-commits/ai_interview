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
  resumes_count?: number;
  roles_count?: number;
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

export interface TopPage {
  path: string;
  visits: number;
  visitors: number;
}

export interface AdminStats {
  users_total: number;
  active_users: number;
  admin_count: number;
  total_logins: number;
  logins_today: number;
  visits_total: number;
  visits_today: number;
  visitors_today: number;
  signups_by_day: DayCount[];
  visits_by_day: DayCount[];
  recent_users: AdminUserRow[];
  system?: SystemStatus | null;
  interviews_total?: number;
  interviews_today?: number;
  questions_total?: number;
  resumes_total?: number;
  roles_total?: number;
  logins_by_day?: DayCount[];
  interviews_by_day?: DayCount[];
  visitors_by_day?: DayCount[];
  top_pages?: TopPage[];
  recent_logins?: AdminUserRow[];
}
