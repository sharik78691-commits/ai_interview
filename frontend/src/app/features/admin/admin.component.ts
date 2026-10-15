import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';
import { AdminService } from '../../core/services/admin.service';
import { AdminStats, AdminUserRow, DayCount } from '../../core/models/admin.models';
import { AuthService } from '../../core/auth/auth.service';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';

function toISODate(d: Date): string {
  const m = `${d.getMonth() + 1}`.padStart(2, '0');
  const day = `${d.getDate()}`.padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

@Component({
  selector: 'app-admin',
  standalone: true,
  imports: [FormsModule, ErrorBannerComponent],
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

  /** Date range driving every section. Default: last 30 days (incl. today). */
  fromDate = '';
  toDate = '';

  ngOnInit(): void {
    const to = new Date();
    const from = new Date();
    from.setDate(to.getDate() - 29);
    this.toDate = toISODate(to);
    this.fromDate = toISODate(from);
    this.loadStats();
    this.loadUsers();
  }

  ngOnDestroy(): void {
    this.subs.forEach((s) => s.unsubscribe());
    if (this.searchTimer) clearTimeout(this.searchTimer);
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

  loadUsers(): void {
    this.loadingUsers = true;
    this.subs.push(
      this.admin
        .users(this.query.trim(), this.page, this.pageSize, this.fromDate || undefined, this.toDate || undefined)
        .subscribe({
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

  private handleError(err: unknown): void {
    const status = (err as { status?: number })?.status;
    if (status === 401 || status === 403) {
      // Signed out mid-session or not an admin: the guard usually redirects
      // first, but a direct API failure still gets a clear message.
      this.forbidden = true;
      return;
    }
    this.error = 'Could not load admin data — is the backend running?';
  }

  // -------------------------------------------------------------------- range

  /** Re-query every section for the chosen From–To window. */
  applyRange(): void {
    this.error = '';
    this.page = 1;
    this.loadStats();
    this.loadUsers();
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

  /** Debounced search: waits for typing to pause before reloading page 1. */
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

  /** Flip an account between active and deactivated (admins excluded). */
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

  /** Grant / revoke admin rights (never your own row). */
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

  /** True for the row of the currently signed-in admin (self actions hidden). */
  isSelf(user: AdminUserRow): boolean {
    const me = this.auth.currentUser?.id;
    return me !== undefined && me !== null && String(me) === String(user.id);
  }

  /** CSV download navigates (session cookie travels automatically). */
  exportCsv(): void {
    window.location.assign(this.admin.exportUrl());
  }

  /** Reload every section (stats + current users page). */
  refresh(): void {
    this.error = '';
    this.loadStats();
    this.loadUsers();
  }

  // ------------------------------------------------------------------- charts

  /** Tallest bar in a series (0-safe) for percentage heights. */
  chartMax(series: DayCount[]): number {
    return Math.max(0, ...series.map((b) => b.count));
  }

  /** Bar height 4–100%: never invisible, never overflowing. */
  barHeight(value: number, max: number): number {
    if (max <= 0 || value <= 0) return 4;
    return Math.max(8, Math.round((value / max) * 100));
  }

  fmtDate(iso: string | null): string {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d.getTime()) ? '—' : d.toLocaleString();
  }

  /** 3661 → "1h 1m"; 90 → "1m 30s"; 45 → "45s". */
  fmtSeconds(total: number): string {
    const s = Math.max(0, Math.round(total || 0));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ${s % 60}s`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }
}
