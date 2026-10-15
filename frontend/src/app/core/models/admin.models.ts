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
  last_location?: string;
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

export interface TopClick {
  label: string;
  path: string;
  count: number;
}

export interface PageTimeRow {
  path: string;
  seconds: number;
  views: number;
}

export interface VisitRow {
  at: string | null;
  path: string;
  email: string;
  location: string;
  device: string;
  visitor: string;
}

export interface LoginRow {
  email: string;
  name: string;
  at: string | null;
  location: string;
}

export interface LiveFeedItem {
  at: string | null;
  text: string;
  location: string;
}

export interface RangeInfo {
  from_date: string;
  to_date: string;
}

export interface AllTime {
  users_total: number;
  active_users: number;
  admin_count: number;
  resumes_total: number;
  roles_total: number;
  total_logins: number;
  visits_total: number;
  interviews_total: number;
  questions_total: number;
}

export interface InRange {
  signups: number;
  logins: number;
  interviews: number;
  questions: number;
  visits: number;
  visitors: number;
  clicks: number;
  seconds: number;
}

export interface AdminStats {
  range: RangeInfo;
  system: SystemStatus;
  all_time: AllTime;
  in_range: InRange;
  signups_by_day: DayCount[];
  logins_by_day: DayCount[];
  visits_by_day: DayCount[];
  interviews_by_day: DayCount[];
  visitors_by_day: DayCount[];
  top_pages: TopPage[];
  top_clicks: TopClick[];
  page_times: PageTimeRow[];
  recent_logins: LoginRow[];
  recent_visits: VisitRow[];
  live_feed: LiveFeedItem[];
  recent_users: AdminUserRow[];
}
