import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../api/api-config';
import {
  AdminPlan,
  AdminSubscriptionRow,
  AdminUserBilling,
  AuditEntry,
  Paged,
  PaymentRecord,
  PlanInput,
  Subscription,
  VerifyResponse,
} from '../models/billing.models';

export interface SubscriptionFilters {
  q?: string;
  plan_id?: number | null;
  status?: string;
  source?: string;
  gateway?: string;
  expires_from?: string;
  expires_to?: string;
  page?: number;
  page_size?: number;
}

export interface PaymentFilters {
  q?: string;
  plan_id?: number | null;
  status?: string;
  gateway?: string;
  page?: number;
  page_size?: number;
}

function params(filters: object): HttpParams {
  let p = new HttpParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== null && v !== '') p = p.set(k, String(v));
  }
  return p;
}

/** Admin subscription & plan management (admin session + CSRF on writes). */
@Injectable({ providedIn: 'root' })
export class AdminBillingService {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  plans(): Observable<AdminPlan[]> {
    return this.http.get<AdminPlan[]>(`${this.base}/api/admin/plans`);
  }

  createPlan(input: PlanInput): Observable<AdminPlan> {
    return this.http.post<AdminPlan>(`${this.base}/api/admin/plans`, input);
  }

  updatePlan(id: number, input: PlanInput): Observable<AdminPlan> {
    return this.http.patch<AdminPlan>(`${this.base}/api/admin/plans/${id}`, input);
  }

  subscriptions(filters: SubscriptionFilters): Observable<Paged<AdminSubscriptionRow>> {
    return this.http.get<Paged<AdminSubscriptionRow>>(`${this.base}/api/admin/subscriptions`, {
      params: params(filters),
    });
  }

  payments(filters: PaymentFilters): Observable<Paged<PaymentRecord>> {
    return this.http.get<Paged<PaymentRecord>>(`${this.base}/api/admin/payments`, {
      params: params(filters),
    });
  }

  userBilling(userId: number): Observable<AdminUserBilling> {
    return this.http.get<AdminUserBilling>(`${this.base}/api/admin/users/${userId}/billing`);
  }

  grant(userId: number, body: { plan_id?: number | null; days?: number | null; reason: string }): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/admin/users/${userId}/subscriptions`, body);
  }

  extend(subId: number, days: number, reason: string): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/admin/subscriptions/${subId}/extend`, { days, reason });
  }

  revoke(subId: number, reason: string): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/admin/subscriptions/${subId}/revoke`, { reason });
  }

  reconcile(paymentId: number): Observable<VerifyResponse> {
    return this.http.post<VerifyResponse>(`${this.base}/api/admin/payments/${paymentId}/reconcile`, {});
  }

  audit(userId?: number): Observable<AuditEntry[]> {
    return this.http.get<AuditEntry[]>(`${this.base}/api/admin/subscription-audit`, {
      params: params({ user_id: userId }),
    });
  }
}
