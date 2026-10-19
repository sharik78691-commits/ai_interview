import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { AdminService } from '../../core/services/admin.service';
import {
  AdminStats,
  AdminUserRow,
  AiUsageRow,
  AiCostReport,
  AiCostWindowKey,
  BusinessReport,
  DailyPoint,
  DayCount,
  PersonRow,
  SttModel,
} from '../../core/models/admin.models';
import { AiModel } from '../../core/models/ai-models';
import { AuthService } from '../../core/auth/auth.service';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';
import { AdminBillingComponent } from './billing/admin-billing.component';

function toISODate(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

export type AdminTab = 'overview' | 'costs' | 'users' | 'billing' | 'system';
export type CostMetric = 'cost' | 'calls';

/** Auto-refresh cadence for the live AI-cost view. */
const COST_REFRESH_MS = 30_000;
const MODEL_COLORS = [
  '#6e8bff', '#34d399', '#fbbf24', '#f87171', '#a06eff',
  '#38bdf8', '#fb923c', '#e879f9', '#2dd4bf', '#94a3b8',
];
export type TrendMetric = 'signups' | 'active' | 'questions';

/** Public plans (same prices as the pricing page) used for the revenue projection. */
export const PLAN_OPTIONS = [
  { key: 'trial', name: '3 Days', price: 150 },
  { key: 'week', name: '7 Days', price: 300 },
  { key: 'month', name: '1 Month', price: 900 },
  { key: 'quarter', name: '3 Months', price: 2000 },
] as const;

const TREND_W = 600;
const TREND_H = 150;

@Component({
  selector: 'app-admin',
  standalone: true,
  imports: [FormsModule, ErrorBannerComponent, AdminBillingComponent],
  templateUrl: './admin.component.html',
  styleUrl: './admin.component.css',
})
export class AdminComponent implements OnInit, OnDestroy {
  private admin = inject(AdminService);
  private auth = inject(AuthService);
  private subs: Subscription[] = [];

  stats: AdminStats | null = null;
  users: AdminUserRow[] = [];
  total = 0;
  page = 1;
  readonly pageSize = 20;
  query = '';
  loadingStats = true;
  loadingUsers = true;
  error = '';
  forbidden = false;
  toggling: Record<number, boolean> = {};
  private searchTimer: ReturnType<typeof setTimeout> | null = null;

  /** AI model catalog + provider health (admin-managed, no secrets). */
  aiModels: AiModel[] = [];
  aiDefault = '';
  aiErrors: Record<string, Record<string, number>> = {};
  loadingModels = true;
  savingModel: Record<string, boolean> = {};

  /** Speech-to-text models (admin enable/disable + default). */
  sttModels: SttModel[] = [];
  sttDefault = '';
  sttActive: string | null = null;
  loadingStt = true;
  savingStt: Record<string, boolean> = {};

  /** Owner-level business report (growth, funnel, retention, insights). */
  tab: AdminTab = 'overview';
  biz: BusinessReport | null = null;
  loadingBiz = true;
  trendMetric: TrendMetric = 'active';
  readonly plans = PLAN_OPTIONS;
  planKey: string = 'month';
  conversionPct = 8;
  readonly weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  readonly hours = Array.from({ length: 24 }, (_, i) => i);
  copiedKey = '';

  /** Live per-model AI usage + cost (auto-refreshes while its tab is open). */
  costs: AiCostReport | null = null;
  loadingCosts = true;
  costWindow: AiCostWindowKey = '7d';
  readonly costWindows: AiCostWindowKey[] = ['24h', '7d', '30d', '90d'];
  costMetric: CostMetric = 'cost';
  includeDemo = false;
  costUpdatedAt: Date | null = null;
  private costTimer: ReturnType<typeof setInterval> | null = null;

  /** Date range driving the dashboard metrics. Default: last 30 days. */
  fromDate = '';
  toDate = '';

  ngOnInit(): void {
    const to = new Date();
    const from = new Date();
    from.setDate(to.getDate() - 29);
    this.toDate = toISODate(to);
    this.fromDate = toISODate(from);
    this.loadStats();
    this.loadBusiness();
    this.loadUsers();
    this.loadAiModels();
    this.loadSttModels();
  }

  ngOnDestroy(): void {
    this.subs.forEach((s) => s.unsubscribe());
    if (this.searchTimer) clearTimeout(this.searchTimer);
    this.stopCostPolling();
  }

  // ------------------------------------------------------------------ loading

  loadStats(): void {
    this.loadingStats = true;
    this.subs.push(
      this.admin.stats(this.fromDate || undefined, this.toDate || undefined).subscribe({
        next: (s) => {
          this.stats = s;
          // Trust the server's normalized range for the labels.
          this.fromDate = s.range.from_date;
          this.toDate = s.range.to_date;
          this.loadingStats = false;
        },
        error: (err) => {
          this.loadingStats = false;
          this.handleError(err);
        },
      }),
    );
  }

  loadBusiness(): void {
    this.loadingBiz = true;
    this.subs.push(
      this.admin.business(this.fromDate || undefined, this.toDate || undefined).subscribe({
        next: (b) => {
          this.biz = b;
          this.loadingBiz = false;
        },
        error: (err) => {
          this.loadingBiz = false;
          this.handleError(err);
        },
      }),
    );
  }

  loadUsers(): void {
    this.loadingUsers = true;
    this.subs.push(
      this.admin.users(this.query.trim(), this.page, this.pageSize).subscribe({
        next: (p) => {
          this.users = p.users;
          this.total = p.total;
          this.page = p.page;
          this.loadingUsers = false;
        },
        error: (err) => {
          this.loadingUsers = false;
          this.handleError(err);
        },
      }),
    );
  }

  loadAiModels(): void {
    this.loadingModels = true;
    this.subs.push(
      this.admin.aiModels().subscribe({
        next: (res) => {
          this.aiModels = res.models ?? [];
          this.aiDefault = res.default ?? '';
          this.aiErrors = res.errors ?? {};
          this.loadingModels = false;
        },
        error: (err) => {
          this.loadingModels = false;
          this.handleError(err);
        },
      }),
    );
  }

  loadSttModels(): void {
    this.loadingStt = true;
    this.subs.push(
      this.admin.sttModels().subscribe({
        next: (res) => {
          this.sttModels = res.models ?? [];
          this.sttDefault = res.default ?? '';
          this.sttActive = res.active ?? null;
          this.loadingStt = false;
        },
        error: (err) => {
          this.loadingStt = false;
          this.handleError(err);
        },
      }),
    );
  }

  toggleSttModel(m: SttModel): void {
    if (this.savingStt[m.key]) return;
    this.savingStt[m.key] = true;
    this.subs.push(
      this.admin.updateSttModel(m.key, { enabled: !m.enabled }).subscribe({
        next: (res) => {
          this.savingStt[m.key] = false;
          this.sttModels = res.models ?? [];
          this.sttDefault = res.default ?? '';
          this.sttActive = res.active ?? null;
        },
        error: (err) => {
          this.savingStt[m.key] = false;
          this.handleError(err);
        },
      }),
    );
  }

  setDefaultSttModel(m: SttModel): void {
    if (this.savingStt[m.key] || m.key === this.sttDefault) return;
    this.savingStt[m.key] = true;
    this.subs.push(
      this.admin.updateSttModel(m.key, { is_default: true }).subscribe({
        next: (res) => {
          this.savingStt[m.key] = false;
          this.sttModels = res.models ?? [];
          this.sttDefault = res.default ?? '';
          this.sttActive = res.active ?? null;
        },
        error: (err) => {
          this.savingStt[m.key] = false;
          this.handleError(err);
        },
      }),
    );
  }

  private handleError(err: unknown): void {
    const status = (err as { status?: number })?.status;
    if (status === 401 || status === 403) {
      this.forbidden = true;
      return;
    }
    this.error = 'Could not load admin data — is the backend running?';
  }

  // -------------------------------------------------------------------- range

  /** Re-query the dashboard metrics for the chosen From–To window. */
  applyRange(): void {
    this.error = '';
    this.loadStats();
    this.loadBusiness();
  }

  /** Quick presets: last N days including today. */
  preset(days: number): void {
    const to = new Date();
    const from = new Date();
    from.setDate(to.getDate() - (days - 1));
    this.toDate = toISODate(to);
    this.fromDate = toISODate(from);
    this.applyRange();
  }

  rangeLabel(): string {
    if (!this.stats) return '';
    return `${this.stats.range.from_date} → ${this.stats.range.to_date}`;
  }

  // -------------------------------------------------------------------- users

  onSearch(): void {
    if (this.searchTimer) clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => {
      this.page = 1;
      this.loadUsers();
    }, 400);
  }

  totalPages(): number {
    return Math.max(1, Math.ceil(this.total / this.pageSize));
  }

  prevPage(): void {
    if (this.page > 1) {
      this.page -= 1;
      this.loadUsers();
    }
  }

  nextPage(): void {
    if (this.page < this.totalPages()) {
      this.page += 1;
      this.loadUsers();
    }
  }

  toggleActive(user: AdminUserRow): void {
    if (user.is_admin || this.toggling[user.id]) return;
    this.toggling[user.id] = true;
    this.subs.push(
      this.admin.setActive(user.id, !user.is_active).subscribe({
        next: (updated) => {
          this.toggling[user.id] = false;
          const i = this.users.findIndex((u) => u.id === updated.id);
          if (i >= 0) this.users[i] = updated;
          this.loadStats();
        },
        error: (err) => {
          this.toggling[user.id] = false;
          this.handleError(err);
        },
      }),
    );
  }

  toggleAdmin(user: AdminUserRow): void {
    if (user.is_admin && this.isSelf(user)) return;
    if (this.toggling[user.id]) return;
    this.toggling[user.id] = true;
    this.subs.push(
      this.admin.setAdmin(user.id, !user.is_admin).subscribe({
        next: (updated) => {
          this.toggling[user.id] = false;
          const i = this.users.findIndex((u) => u.id === updated.id);
          if (i >= 0) this.users[i] = updated;
          this.loadStats();
        },
        error: (err) => {
          this.toggling[user.id] = false;
          this.handleError(err);
        },
      }),
    );
  }

  isSelf(user: AdminUserRow): boolean {
    const me = this.auth.currentUser?.id;
    return me !== undefined && me !== null && String(me) === String(user.id);
  }

  exportCsv(): void {
    window.location.assign(this.admin.exportUrl());
  }

  refresh(): void {
    this.error = '';
    this.loadStats();
    this.loadBusiness();
    this.loadUsers();
    this.loadAiModels();
    this.loadSttModels();
    if (this.tab === 'costs') this.loadCosts();
  }

  // ------------------------------------------------------- AI model management

  modelHealth(provider: string): string {
    const stats = this.aiErrors[provider];
    if (!stats) return 'no calls yet';
    const total = Object.values(stats).reduce((a, b) => a + b, 0);
    const bad = Object.entries(stats)
      .filter(([k]) => k !== 'ok')
      .map(([k, v]) => `${k}: ${v}`)
      .join(', ');
    return bad ? `${total} calls · ${bad}` : `${total} calls · all ok`;
  }

  toggleModel(m: AiModel): void {
    if (this.savingModel[m.key]) return;
    this.savingModel[m.key] = true;
    this.subs.push(
      this.admin.updateAiModel(m.key, { enabled: !m.enabled }).subscribe({
        next: (res) => {
          this.savingModel[m.key] = false;
          this.aiModels = res.models ?? [];
          this.aiDefault = res.default ?? '';
        },
        error: (err) => {
          this.savingModel[m.key] = false;
          this.handleError(err);
        },
      }),
    );
  }

  setDefaultModel(m: AiModel): void {
    if (this.savingModel[m.key] || m.key === this.aiDefault) return;
    this.savingModel[m.key] = true;
    this.subs.push(
      this.admin.updateAiModel(m.key, { is_default: true }).subscribe({
        next: (res) => {
          this.savingModel[m.key] = false;
          this.aiModels = res.models ?? [];
          this.aiDefault = res.default ?? '';
        },
        error: (err) => {
          this.savingModel[m.key] = false;
          this.handleError(err);
        },
      }),
    );
  }

  // ------------------------------------------------------------------- charts

  chartMax(series: DayCount[]): number {
    return Math.max(0, ...series.map((b) => b.count));
  }

  barHeight(value: number, max: number): number {
    if (max <= 0 || value <= 0) return 4;
    return Math.max(8, Math.round((value / max) * 100));
  }

  aiErrorRate(row: AiUsageRow): string {
    if (!row.calls) return '—';
    return `${Math.round((row.errors / row.calls) * 100)}%`;
  }

  fmtDate(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d.getTime()) ? '—' : d.toLocaleString();
  }

  // ------------------------------------------------------- business overview

  setTab(tab: AdminTab): void {
    this.tab = tab;
    if (tab === 'costs') this.startCostPolling();
    else this.stopCostPolling();
  }

  // ------------------------------------------------------------- AI cost view

  private startCostPolling(): void {
    this.loadCosts();
    if (!this.costTimer) {
      this.costTimer = setInterval(() => this.loadCosts(true), COST_REFRESH_MS);
    }
  }

  private stopCostPolling(): void {
    if (this.costTimer) {
      clearInterval(this.costTimer);
      this.costTimer = null;
    }
  }

  loadCosts(silent = false): void {
    if (!silent) this.loadingCosts = true;
    this.subs.push(
      this.admin.aiCosts(this.includeDemo).subscribe({
        next: (c) => {
          this.costs = c;
          this.costUpdatedAt = new Date();
          this.loadingCosts = false;
        },
        error: (err) => {
          this.loadingCosts = false;
          if (!silent) this.handleError(err);
        },
      }),
    );
  }

  toggleDemoCosts(): void {
    this.includeDemo = !this.includeDemo;
    this.loadCosts();
  }

  costWin() {
    return this.costs?.windows[this.costWindow] ?? null;
  }

  windowLabel(w: AiCostWindowKey): string {
    return { '24h': 'Last 24 hours', '7d': 'Last 7 days', '30d': 'Last 30 days', '90d': 'Last 90 days' }[w];
  }

  /** Stable colour per model key (catalog order). */
  modelColor(key: string): string {
    const keys = (this.costs?.pricing ?? []).map((p) => p.key);
    const i = keys.indexOf(key);
    return MODEL_COLORS[(i < 0 ? keys.length : i) % MODEL_COLORS.length];
  }

  /** Stacked bars for the selected window, one column per hour/day. */
  costBars(): { label: string; total: number; heightPct: number; parts: { key: string; pct: number; v: number }[] }[] {
    const series = this.costs?.series[this.costWindow] ?? [];
    const val = (b: { calls: number; cost_inr: number }) => (this.costMetric === 'cost' ? b.cost_inr : b.calls);
    const totals = series.map((b) => Object.values(b.by_model).reduce((a, m) => a + val(m), 0));
    const max = Math.max(0, ...totals);
    return series.map((b, i) => ({
      label: b.label,
      total: totals[i],
      heightPct: max > 0 ? Math.max(totals[i] > 0 ? 4 : 0, (totals[i] / max) * 100) : 0,
      parts: Object.entries(b.by_model)
        .map(([key, m]) => ({ key, v: val(m), pct: totals[i] ? (val(m) * 100) / totals[i] : 0 }))
        .filter((p) => p.v > 0),
    }));
  }

  costBarTitle(b: { label: string; total: number; parts: { key: string; v: number }[] }): string {
    const fmt = (v: number) => (this.costMetric === 'cost' ? this.inr2(v) : `${v} calls`);
    const lines = b.parts.map((p) => `${p.key}: ${fmt(p.v)}`);
    return `${b.label} — ${fmt(b.total)}${lines.length ? '\n' + lines.join('\n') : ''}`;
  }

  /** Models that actually appear in the selected window (legend). */
  costLegend(): { key: string; name: string }[] {
    const w = this.costWin();
    return (w?.models ?? []).filter((m) => m.calls > 0).map((m) => ({ key: m.key, name: m.display_name }));
  }

  /** Rupees with paise — AI calls cost fractions of a rupee. */
  inr2(n: number | null | undefined): string {
    if (n === null || n === undefined) return '—';
    return '₹' + n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: n < 1 ? 3 : 2 });
  }

  costShare(m: { cost_inr: number }): number {
    const t = this.costWin()?.totals.cost_inr ?? 0;
    return t ? (m.cost_inr * 100) / t : 0;
  }

  updatedLabel(): string {
    return this.costUpdatedAt ? this.costUpdatedAt.toLocaleTimeString() : '';
  }

  /** "+12.5%" / "−4%" / "new" — period-over-period change label. */
  deltaLabel(delta: number | null, value: number): string {
    if (delta === null) return value > 0 ? 'new' : '—';
    const sign = delta > 0 ? '+' : delta < 0 ? '−' : '';
    return `${sign}${Math.abs(delta)}%`;
  }

  deltaClass(delta: number | null): string {
    if (delta === null || delta === 0) return 'flat';
    return delta > 0 ? 'up' : 'down';
  }

  private series(metric: TrendMetric): number[] {
    return (this.biz?.daily ?? []).map((d: DailyPoint) => d[metric]);
  }

  /** SVG polyline points for a tiny trend line (KPI cards). */
  sparkPoints(metric: TrendMetric, w = 110, h = 34): string {
    const v = this.series(metric);
    if (v.length < 2) return '';
    const max = Math.max(1, ...v);
    return v
      .map((n, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h - 3 - (n / max) * (h - 6)).toFixed(1)}`)
      .join(' ');
  }

  /** Big trend chart: area path + line path for the selected metric. */
  trendPaths(): { line: string; area: string; max: number } {
    const v = this.series(this.trendMetric);
    const max = Math.max(1, ...v);
    if (v.length < 2) return { line: '', area: '', max };
    const pts = v.map((n, i) => [
      (i / (v.length - 1)) * TREND_W,
      TREND_H - 6 - (n / max) * (TREND_H - 18),
    ]);
    const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
    return { line, area: `${line} L${TREND_W},${TREND_H} L0,${TREND_H} Z`, max };
  }

  trendTotal(): number {
    return this.series(this.trendMetric).reduce((a, b) => a + b, 0);
  }

  trendEnds(): [string, string] {
    const d = this.biz?.daily ?? [];
    return [d[0]?.day.slice(5) ?? '', d[d.length - 1]?.day.slice(5) ?? ''];
  }

  funnelDrop(i: number): number | null {
    const f = this.biz?.funnel ?? [];
    if (i === 0 || !f[i - 1]?.count) return null;
    return Math.round(((f[i - 1].count - f[i].count) * 100) / f[i - 1].count);
  }

  segmentTotal(): number {
    return (this.biz?.segments ?? []).reduce((a, s) => a + s.count, 0);
  }

  segmentWidth(count: number): number {
    const t = this.segmentTotal();
    return t ? (count * 100) / t : 0;
  }

  heatMax(): number {
    return Math.max(1, ...(this.biz?.heatmap ?? []).flat());
  }

  heatStyle(v: number): string {
    if (!v) return 'transparent';
    const a = 0.18 + (v / this.heatMax()) * 0.82;
    return `rgba(110, 139, 255, ${a.toFixed(2)})`;
  }

  /** Busiest weekday+hour (IST) for the "when to run campaigns" hint. */
  peakSlot(): string {
    const h = this.biz?.heatmap ?? [];
    let best = 0;
    let at = '';
    h.forEach((row, d) =>
      row.forEach((v, hr) => {
        if (v > best) {
          best = v;
          at = `${this.weekdays[d]} ${this.hourLabel(hr)}`;
        }
      }),
    );
    return at;
  }

  hourLabel(h: number): string {
    const ampm = h < 12 ? 'am' : 'pm';
    return `${h % 12 === 0 ? 12 : h % 12}${ampm}`;
  }

  // Revenue potential is a PROJECTION: payments are not tracked in-app yet.
  selectedPlan() {
    return this.plans.find((p) => p.key === this.planKey) ?? this.plans[2];
  }

  projectedBuyers(): number {
    return Math.round(((this.biz?.kpis.active_users.value ?? 0) * this.conversionPct) / 100);
  }

  projectedRevenue(): number {
    return this.projectedBuyers() * this.selectedPlan().price;
  }

  inr(n: number): string {
    return '₹' + Math.round(n).toLocaleString('en-IN');
  }

  num(n: number | null | undefined): string {
    return n === null || n === undefined ? '—' : n.toLocaleString('en-IN');
  }

  tokensK(n: number): string {
    return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
  }

  mailtoAll(list: PersonRow[], subject: string): string {
    const bcc = list.map((p) => p.email).join(',');
    return `mailto:?bcc=${encodeURIComponent(bcc)}&subject=${encodeURIComponent(subject)}`;
  }

  copyEmails(key: string, list: PersonRow[]): void {
    const text = list.map((p) => p.email).join(', ');
    navigator.clipboard?.writeText(text).then(
      () => {
        this.copiedKey = key;
        setTimeout(() => (this.copiedKey = ''), 1800);
      },
      () => undefined,
    );
  }

  levelIcon(level: string): string {
    return level === 'action' ? '🎯' : level === 'warn' ? '⚠️' : '✅';
  }
}
