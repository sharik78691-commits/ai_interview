import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AdminStats, AdminUserRow, AdminUsersPage } from '../models/admin.models';
import { AiModel } from '../models/ai-models';

const BASE = environment.apiBaseUrl;

/**
 * Admin-panel API client. Every endpoint requires an admin session — the
 * backend answers 401 (signed out) or 403 (not an admin), which the caller
 * surfaces instead of rendering empty tables.
 */
@Injectable({ providedIn: 'root' })
export class AdminService {
  private http = inject(HttpClient);

  /** Headline counters, series and tables for a date range (YYYY-MM-DD). */
  stats(from?: string, to?: string): Observable<AdminStats> {
    let params = new HttpParams();
    if (from) params = params.set('from', from);
    if (to) params = params.set('to', to);
    return this.http.get<AdminStats>(`${BASE}/api/admin/stats`, { params });
  }

  /** Searchable, paginated user list (newest first), optional signup range. */
  users(query = '', page = 1, pageSize = 20, from?: string, to?: string): Observable<AdminUsersPage> {
    let params = new HttpParams()
      .set('q', query)
      .set('page', page)
      .set('page_size', pageSize);
    if (from) params = params.set('from', from);
    if (to) params = params.set('to', to);
    return this.http.get<AdminUsersPage>(`${BASE}/api/admin/users`, { params });
  }

  /** Activate / deactivate an account (admins cannot deactivate themselves). */
  setActive(id: number, isActive: boolean): Observable<AdminUserRow> {
    return this.http.patch<AdminUserRow>(`${BASE}/api/admin/users/${id}`, {
      is_active: isActive,
    });
  }

  /** Grant / revoke admin rights (admins cannot demote themselves). */
  setAdmin(id: number, isAdmin: boolean): Observable<AdminUserRow> {
    return this.http.patch<AdminUserRow>(`${BASE}/api/admin/users/${id}`, {
      is_admin: isAdmin,
    });
  }

  /** CSV download URL (cookie auth travels on full-page navigation). */
  exportUrl(): string {
    return `${BASE}/api/admin/users/export`;
  }

  /** Model catalog + default + provider error stats (no secrets). */
  aiModels(): Observable<{ models: AiModel[]; default: string; errors: Record<string, Record<string, number>> }> {
    return this.http.get<{ models: AiModel[]; default: string; errors: Record<string, Record<string, number>> }>(
      `${BASE}/api/admin/ai-models`,
    );
  }

  /** Update admin-editable model flags (enabled/default/labels/priority). */
  updateAiModel(
    key: string,
    patch: { enabled?: boolean; is_default?: boolean; display_name?: string; description?: string; free_tier?: boolean; priority?: number },
  ): Observable<{ models: AiModel[]; default: string }> {
    return this.http.patch<{ models: AiModel[]; default: string }>(
      `${BASE}/api/admin/ai-models/${encodeURIComponent(key)}`,
      patch,
    );
  }
}
