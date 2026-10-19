import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AdminBillingService, PaymentFilters, SubscriptionFilters } from '../../../core/services/admin-billing.service';
import {
  AdminPlan,
  AdminSubscriptionRow,
  AdminUserBilling,
  Paged,
  PaymentRecord,
  PlanInput,
  Subscription,
  durationLabel,
  formatMoney,
} from '../../../core/models/billing.models';

type View = 'subscriptions' | 'payments' | 'plans';

/** Editable copy of a plan with money in MAJOR units for the form. */
interface PlanForm {
  id: number | null;
  code: string;
  name: string;
  description: string;
  price: number;
  currency: string;
  duration_days: number;
  trial_days: number;
  promo_price: number | null;
  promo_label: string;
  promo_starts_at: string;
  promo_ends_at: string;
  features: string;
  badge: string;
  is_highlighted: boolean;
  is_active: boolean;
  sort_order: number;
}

const toMinor = (major: number | null) => (major === null || Number.isNaN(major) ? null : Math.round(major * 100));
const isoOrNull = (local: string) => (local ? new Date(local).toISOString() : null);
const localInput = (iso: string | null) => {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

/**
 * Admin: plans, subscriptions, payments, and manual subscription changes.
 *
 * Every write goes through the backend's admin + CSRF checks and is written
 * to the subscription audit log with the acting admin and a reason. Manual
 * grants are separate from payments (no money is recorded for them).
 */
@Component({
  selector: 'app-admin-billing',
  standalone: true,
  imports: [FormsModule, DatePipe],
  templateUrl: './admin-billing.component.html',
  styleUrl: './admin-billing.component.css',
})
export class AdminBillingComponent implements OnInit {
  private readonly api = inject(AdminBillingService);

  readonly money = formatMoney;
  readonly duration = durationLabel;

  view: View = 'subscriptions';
  readonly error = signal('');
  readonly info = signal('');

  // ------------------------------------------------------------- plans
  readonly plans = signal<AdminPlan[]>([]);
  planForm: PlanForm | null = null;

  // ----------------------------------------------------- subscriptions
  subFilters: SubscriptionFilters = { q: '', plan_id: null, status: '', source: '', gateway: '', expires_from: '', expires_to: '', page: 1, page_size: 25 };
  readonly subs = signal<Paged<AdminSubscriptionRow> | null>(null);

  // ---------------------------------------------------------- payments
  payFilters: PaymentFilters = { q: '', plan_id: null, status: '', gateway: '', page: 1, page_size: 25 };
  readonly payments = signal<Paged<PaymentRecord> | null>(null);

  // ------------------------------------------------------- user detail
  readonly detail = signal<AdminUserBilling | null>(null);
  userLookup = '';
  grantPlanId: number | null = null;
  grantDays: number | null = null;
  grantReason = '';
  actionDays = 7;
  actionReason = '';
  readonly busy = signal(false);

  ngOnInit(): void {
    this.loadPlans();
    this.loadSubs();
  }

  setView(v: View): void {
    this.view = v;
    this.error.set('');
    if (v === 'payments' && !this.payments()) this.loadPayments();
    if (v === 'plans') this.loadPlans();
  }

  // ------------------------------------------------------------- loads
  loadPlans(): void {
    this.api.plans().subscribe({ next: (p) => this.plans.set(p), error: (e) => this.fail(e) });
  }

  loadSubs(page = 1): void {
    this.subFilters.page = page;
    this.api.subscriptions(this.subFilters).subscribe({ next: (r) => this.subs.set(r), error: (e) => this.fail(e) });
  }

  loadPayments(page = 1): void {
    this.payFilters.page = page;
    this.api.payments(this.payFilters).subscribe({ next: (r) => this.payments.set(r), error: (e) => this.fail(e) });
  }

  pages(p: Paged<unknown> | null): number {
    return p ? Math.max(1, Math.ceil(p.total / p.page_size)) : 1;
  }

  // ------------------------------------------------------- user detail
  openUser(userId: number): void {
    this.error.set('');
    this.api.userBilling(userId).subscribe({
      next: (d) => {
        this.detail.set(d);
        this.grantReason = '';
        this.actionReason = '';
      },
      error: (e) => this.fail(e),
    });
  }

  lookupUser(): void {
    const id = Number(this.userLookup.trim());
    if (Number.isInteger(id) && id > 0) {
      this.openUser(id);
      return;
    }
    // Email: find via the subscription list (exact user id needed for detail).
    this.subFilters.q = this.userLookup.trim();
    this.loadSubs();
    this.view = 'subscriptions';
  }

  closeUser(): void {
    this.detail.set(null);
  }

  grant(): void {
    const d = this.detail();
    if (!d || this.busy()) return;
    if (this.grantReason.trim().length < 3) return this.warn('Enter a reason (it is stored in the audit log).');
    if (!this.grantPlanId && !this.grantDays) return this.warn('Choose a plan or enter a number of days.');
    this.run(this.api.grant(d.user_id, { plan_id: this.grantPlanId, days: this.grantDays, reason: this.grantReason.trim() }), 'Subscription granted.', d.user_id);
  }

  extend(s: Subscription): void {
    if (this.actionReason.trim().length < 3) return this.warn('Enter a reason before extending.');
    this.run(this.api.extend(s.id, this.actionDays, this.actionReason.trim()), `Extended by ${this.actionDays} days.`, s.user_id);
  }

  revoke(s: Subscription): void {
    if (this.actionReason.trim().length < 3) return this.warn('Enter a reason before revoking.');
    if (!confirm(`Revoke "${s.plan_name}" for ${s.user_email}? Access ends immediately. Refunds are NOT issued automatically.`)) return;
    this.run(this.api.revoke(s.id, this.actionReason.trim()), 'Subscription revoked.', s.user_id);
  }

  reconcile(p: PaymentRecord): void {
    this.busy.set(true);
    this.api.reconcile(p.id).subscribe({
      next: (r) => {
        this.busy.set(false);
        this.info.set(`Payment #${p.id}: ${r.status}. ${r.message}`);
        this.loadPayments(this.payFilters.page ?? 1);
      },
      error: (e) => {
        this.busy.set(false);
        this.fail(e);
      },
    });
  }

  private run(obs: ReturnType<AdminBillingService['grant']>, ok: string, userId: number): void {
    this.busy.set(true);
    this.error.set('');
    obs.subscribe({
      next: () => {
        this.busy.set(false);
        this.info.set(ok);
        this.openUser(userId);
        this.loadSubs(this.subFilters.page ?? 1);
      },
      error: (e) => {
        this.busy.set(false);
        this.fail(e);
      },
    });
  }

  // -------------------------------------------------------------- plans
  newPlan(): void {
    this.planForm = {
      id: null, code: '', name: '', description: '', price: 0, currency: 'INR', duration_days: 30,
      trial_days: 0, promo_price: null, promo_label: '', promo_starts_at: '', promo_ends_at: '',
      features: '', badge: '', is_highlighted: false, is_active: true, sort_order: (this.plans().length + 1) * 10,
    };
  }

  editPlan(p: AdminPlan): void {
    this.planForm = {
      id: p.id, code: p.code, name: p.name, description: p.description, price: p.regular_price_minor / 100,
      currency: p.currency, duration_days: p.duration_days, trial_days: p.trial_days,
      promo_price: p.promo_price_minor === null ? null : p.promo_price_minor / 100,
      promo_label: p.promo_label, promo_starts_at: localInput(p.promo_starts_at),
      promo_ends_at: localInput(p.promo_ends_at), features: p.features.join('\n'), badge: p.badge,
      is_highlighted: p.is_highlighted, is_active: p.is_active, sort_order: p.sort_order,
    };
  }

  savePlan(): void {
    const f = this.planForm;
    if (!f || this.busy()) return;
    const hasPromo = f.promo_price !== null && `${f.promo_price}` !== '';
    const body: PlanInput = {
      name: f.name.trim(), description: f.description, price_minor: toMinor(f.price) ?? 0,
      currency: f.currency.trim().toUpperCase(), duration_days: Number(f.duration_days),
      trial_days: Number(f.trial_days), badge: f.badge.trim(), is_highlighted: f.is_highlighted,
      is_active: f.is_active, sort_order: Number(f.sort_order),
      features: f.features.split('\n').map((x) => x.trim()).filter(Boolean),
    };
    if (hasPromo) {
      Object.assign(body, {
        promo_price_minor: toMinor(Number(f.promo_price)), promo_label: f.promo_label.trim(),
        promo_starts_at: isoOrNull(f.promo_starts_at), promo_ends_at: isoOrNull(f.promo_ends_at),
      });
    } else if (f.id) {
      body.clear_promo = true;
    }
    const req = f.id ? this.api.updatePlan(f.id, body) : this.api.createPlan({ ...body, code: f.code.trim() });
    this.busy.set(true);
    req.subscribe({
      next: (p) => {
        this.busy.set(false);
        this.planForm = null;
        this.info.set(`Plan "${p.name}" saved. The public pricing page shows the change immediately.`);
        this.loadPlans();
      },
      error: (e) => {
        this.busy.set(false);
        this.fail(e);
      },
    });
  }

  togglePlan(p: AdminPlan): void {
    this.api.updatePlan(p.id, { is_active: !p.is_active }).subscribe({
      next: () => this.loadPlans(),
      error: (e) => this.fail(e),
    });
  }

  // -------------------------------------------------------------- misc
  private warn(text: string): void {
    this.error.set(text);
  }

  private fail(err: unknown): void {
    if (err instanceof HttpErrorResponse) {
      const d = err.error?.detail;
      if (typeof d === 'string') return this.error.set(d);
      if (Array.isArray(d) && d[0]?.msg) return this.error.set(String(d[0].msg));
      if (err.status === 403) return this.error.set('Admin access required (or your session needs a refresh).');
    }
    this.error.set('Request failed. Please try again.');
  }
}
