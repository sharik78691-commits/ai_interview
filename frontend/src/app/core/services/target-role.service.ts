import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, switchMap, tap } from 'rxjs';
import { environment } from '../../../environments/environment';

const BASE = environment.apiBaseUrl;

/** One saved target role in the server-side library (list view). */
export interface TargetRole {
  id: number;
  position: string;
  chars: number;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

/** Full saved role with the editable job description. */
export interface TargetRoleDetail extends TargetRole {
  job_description: string;
}

/**
 * Server-side target-role library (mirrors the resume library).
 * The dashboard keeps its existing local inputs; this service only syncs
 * them with the account's stored roles.
 */
@Injectable({ providedIn: 'root' })
export class TargetRoleService {
  private http = inject(HttpClient);

  /** Local inputs (the editor). Empty = nothing typed yet. */
  get localPosition(): string {
    return localStorage.getItem('aia_position') ?? '';
  }

  get localDescription(): string {
    return localStorage.getItem('aia_jd') ?? '';
  }

  saveLocal(position: string, description: string): void {
    localStorage.setItem('aia_position', position);
    localStorage.setItem('aia_jd', description);
  }

  /** The signed-in user's saved roles, newest first. Empty when signed out. */
  listRoles(): Observable<TargetRole[]> {
    return this.http.get<TargetRole[]>(`${BASE}/api/roles`).pipe(
      catchError(() => of([])),
    );
  }

  /** Full saved role with job description. */
  getRole(id: number): Observable<TargetRoleDetail> {
    return this.http.get<TargetRoleDetail>(`${BASE}/api/roles/${id}`);
  }

  /** Save the current inputs as a role (becomes the active one). */
  createRole(position: string, jobDescription: string): Observable<TargetRoleDetail> {
    return this.http.post<TargetRoleDetail>(`${BASE}/api/roles`, {
      position,
      job_description: jobDescription,
    });
  }

  /** Make a saved role the active one. */
  selectRole(id: number): Observable<TargetRoleDetail> {
    return this.http.patch<TargetRoleDetail>(`${BASE}/api/roles/${id}`, {
      is_active: true,
    });
  }

  /** Delete a saved role (newest remaining one becomes active). */
  deleteRole(id: number): Observable<{ message: string }> {
    return this.http.delete<{ message: string }>(`${BASE}/api/roles/${id}`);
  }

  /**
   * Adopt the server's active role into the local inputs when they are empty
   * (fresh browser / new device). Never overwrites what the user typed.
   * Resolves the adopted detail, or null when there is nothing to adopt.
   */
  adoptActiveRole(): Observable<TargetRoleDetail | null> {
    if (this.localPosition.trim() || this.localDescription.trim()) {
      return of(null);
    }
    return this.listRoles().pipe(
      map((rows) => rows.find((r) => r.is_active) ?? null),
      switchMap((row) => {
        if (!row) return of(null);
        return this.getRole(row.id).pipe(
          tap((d) => this.saveLocal(d.position || '', d.job_description || '')),
          catchError(() => of(null)),
        );
      }),
      catchError(() => of(null)),
    );
  }
}
