import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AdminStats, AdminUserRow, AdminUsersPage } from '../models/admin.models';

const BASE = environment.apiBaseUrl;

/**
 * Admin-panel API client. Every endpoint requires an admin session — the
 * backend answers 401 (signed out) or 403 (not an admin), which the caller
 * surfaces instead of rendering empty tables.
 */
@Injectable({ providedIn: 'root' })
export class AdminService {
  private http = inject(HttpClient);

  /** Headline counters, 14-day series and recent signups. */
  stats(): Observable<AdminStats> {
    return this.http.get<AdminStats>(`${BASE}/api/admin/stats`);
  }

  /** Searchable, paginated user list (newest first). */
  users(query = '', page = 1, pageSize = 20): Observable<AdminUsersPage> {
    const params = new HttpParams()
      .set('q', query)
      .set('page', page)
      .set('page_size', pageSize);
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
}
