import { DatePipe, DecimalPipe, JsonPipe, KeyValuePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Observable } from 'rxjs';
import {
  AdminBillingService,
  AuditFilters,
  PaymentDetail,
  PaymentFilters,
  SubscriptionFilters,
  UsageFilters,
} from '../../../core/services/admin-billing.service';
import {
  AdminGatewayOption,
  AdminPaymentSettings,
  AdminPlan,
  AdminSubscriptionRow,
  AdminUserBilling,
  AuditEntry,
  BillingOverview,
  Currency,
  Paged,
  PaymentRecord,
  PlanInput,
  PaymentSettingsUpdate,
  PlanPrice,
  SelectableGateway,
  Subscription,
  UsageRecord,
  billingTypeLabel,
  formatMoney,
  hoursLabel,
} from '../../../core/models/billing.models';

type View = 'overview' | 'subscriptions' | 'payments' | 'usage' | 'plans' | 'audit';

/** Editable copy of a plan with money in MAJOR units for the form. */
interface PlanForm {
  id: number | null;
  code: string;
  name: string;
  description: string;
  price: number;
  currency: string;
  duration_value: number;
  duration_unit: 'hours' | 'days';
  screenshot_allowance: number;
  billing_type: 'one_time' | 'recurring';
  billing_interval_days: number | null;
  trial_days: number;
  promo_price: number | null;
  promo_label: string;
  promo_starts_at: string;
  promo_ends_at: string;
  features: string;
  badge: string;
  is_highlighted: boolean;
  is_active: boolean;
  is_public: boolean;
  sort_order: number;
  reason: string;
}

/** One currency price being edited for a plan. */
interface PriceForm {
  plan: AdminPlan;
  currency: string;
  amount: number | null;
  is_active: boolean;
  effective_from: string;
  effective_to: string;
  razorpay_plan_id: string;
  reason: string;
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
 * Admin: billing overview, plans & per-currency prices, currencies,
 * subscriptions, payments, screenshot usage, audit log and manual changes.
 *
 * Every write goes through the backend's admin + CSRF checks and is written
 * to the subscription audit log with the acting admin and a reason. Manual
 * grants are separate from payments (no money is recorded for them). Revenue
 * is always shown per currency — INR and USD are never added together.
 */
@Component({
  selector: 'app-admin-billing',
  standalone: true,
  imports: [FormsModule, DatePipe, DecimalPipe, JsonPipe, KeyValuePipe],
  templateUrl: './admin-billing.component.html',
  styleUrl: './admin-billing.component.css',
})
export class AdminBillingComponent implements OnInit {
  private readonly api = inject(AdminBillingService);

  readonly money = formatMoney;
  readonly hours = hoursLabel;
  readonly billingLabel = billingTypeLabel;

  view: View = 'overview';
  readonly error = signal('');
  readonly info = signal('');

  // ---------------------------------------------------------- overview
  readonly overview = signal<BillingOverview | null>(null);
  range = { date_from: '', date_to: '' };

  // ------------------------------------------------------------- plans
  readonly plans = signal<AdminPlan[]>([]);
  readonly currencies = signal<Currency[]>([]);
  planForm: PlanForm | null = null;
  priceForm: PriceForm | null = null;
  readonly priceHistory = signal<{ plan: AdminPlan; rows: PlanPrice[] } | null>(null);
  currencyReason = '';

  // --------------------------------------------- payments on/off + gateway
  readonly paySettings = signal<AdminPaymentSettings | null>(null);
  paySettingsReason = '';

  // ----------------------------------------------------- subscriptions
  subFilters: SubscriptionFilters = {
    q: '', plan_id: null, status: '', source: '', gateway: '', currency: '', payment_status: '',
    created_from: '', created_to: '', starts_from: '', starts_to: '', expires_from: '', expires_to: '',
    sort: 'expires_at', order: 'desc', page: 1, page_size: 25,
  };
  readonly subs = signal<Paged<AdminSubscriptionRow> | null>(null);

  // ---------------------------------------------------------- payments
  payFilters: PaymentFilters = {
    q: '', plan_id: null, status: '', gateway: '', currency: '', date_from: '', date_to: '',
    sort: 'created_at', order: 'desc', page: 1, page_size: 25,
  };
  readonly payments = signal<Paged<PaymentRecord> | null>(null);
  readonly paymentDetail = signal<PaymentDetail | null>(null);

  // ------------------------------------------------------------- usage
  usageFilters: UsageFilters = { q: '', plan_id: null, status: '', retried: false, date_from: '', date_to: '', page: 1, page_size: 50 };
  readonly usage = signal<Paged<UsageRecord> | null>(null);

  // ------------------------------------------------------------- audit
  auditFilters: AuditFilters = { user_id: null, action: '', date_from: '', date_to: '', limit: 200 };
  readonly auditRows = signal<AuditEntry[]>([]);

  // ------------------------------------------------------- user detail
  readonly detail = signal<AdminUserBilling | null>(null);
  userLookup = '';
  grantPlanId: number | null = null;
  grantDays: number | null = null;
  grantCredits: number | null = null;
  grantReason = '';
  actionDays = 7;
  actionReason = '';
  creditDraft: Record<number, number> = {};
  readonly busy = signal(false);

  ngOnInit(): void {
    this.loadPlans();
    this.loadCurrencies();
    this.loadOverview();
    this.loadPaymentSettings();
  }

  setView(v: View): void {
    this.view = v;
    this.error.set('');
    if (v === 'overview') this.loadOverview();
    if (v === 'subscriptions' && !this.subs()) this.loadSubs();
    if (v === 'payments' && !this.payments()) this.loadPayments();
    if (v === 'usage') this.loadUsage();
    if (v === 'audit') this.loadAudit();
    if (v === 'plans') {
      this.loadPlans();
      this.loadCurrencies();
      this.loadPaymentSettings();
    }
  }

  // ------------------------------------------------------------- loads
  loadOverview(): void {
    this.api.overview(this.range).subscribe({ next: (o) => this.overview.set(o), error: (e) => this.fail(e) });
  }

  loadPlans(): void {
    this.api.plans().subscribe({ next: (p) => this.plans.set(p), error: (e) => this.fail(e) });
  }

  loadCurrencies(): void {
    this.api.currencies().subscribe({ next: (c) => this.currencies.set(c), error: (e) => this.fail(e) });
  }

  loadPaymentSettings(): void {
    this.api.paymentSettings().subscribe({ next: (p) => this.paySettings.set(p), error: (e) => this.fail(e) });
  }

  loadSubs(page = 1): void {
    this.subFilters.page = page;
    this.api.subscriptions(this.subFilters).subscribe({ next: (r) => this.subs.set(r), error: (e) => this.fail(e) });
  }

  sortSubs(col: string): void {
    if (this.subFilters.sort === col) this.subFilters.order = this.subFilters.order === 'asc' ? 'desc' : 'asc';
    else {
      this.subFilters.sort = col;
      this.subFilters.order = 'desc';
    }
    this.loadSubs();
  }

  loadPayments(page = 1): void {
    this.payFilters.page = page;
    this.api.payments(this.payFilters).subscribe({ next: (r) => this.payments.set(r), error: (e) => this.fail(e) });
  }

  sortPayments(col: string): void {
    if (this.payFilters.sort === col) this.payFilters.order = this.payFilters.order === 'asc' ? 'desc' : 'asc';
    else {
      this.payFilters.sort = col;
      this.payFilters.order = 'desc';
    }
    this.loadPayments();
  }

  loadUsage(page = 1): void {
    this.usageFilters.page = page;
    this.api.usage(this.usageFilters).subscribe({ next: (r) => this.usage.set(r), error: (e) => this.fail(e) });
  }

  loadAudit(): void {
    this.api.audit(this.auditFilters).subscribe({ next: (r) => this.auditRows.set(r), error: (e) => this.fail(e) });
  }

  openPayment(p: PaymentRecord): void {
    this.api.paymentDetail(p.id).subscribe({ next: (d) => this.paymentDetail.set(d), error: (e) => this.fail(e) });
  }

  pages(p: Paged<unknown> | null): number {
    return p ? Math.max(1, Math.ceil(p.total / p.page_size)) : 1;
  }

  sortMark(current: string | undefined, order: string | undefined, col: string): string {
    return current === col ? (order === 'asc' ? ' ▲' : ' ▼') : '';
  }

  // ------------------------------------------------------- user detail
  openUser(userId: number): void {
    this.error.set('');
    this.api.userBilling(userId).subscribe({
      next: (d) => {
        this.detail.set(d);
        this.grantReason = '';
        this.actionReason = '';
        this.creditDraft = Object.fromEntries(d.subscriptions.map((s) => [s.id, s.screenshot_allowance]));
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

  isLive(s: Subscription): boolean {
    return s.status === 'active' || s.status === 'scheduled';
  }

  grant(): void {
    const d = this.detail();
    if (!d || this.busy()) return;
    if (this.grantReason.trim().length < 3) return this.warn('Enter a reason (it is stored in the audit log).');
    if (!this.grantPlanId && !this.grantDays) return this.warn('Choose a plan or enter a number of days.');
    this.run(
      this.api.grant(d.user_id, {
        plan_id: this.grantPlanId,
        days: this.grantDays,
        screenshot_credits: this.grantCredits,
        reason: this.grantReason.trim(),
      }),
      'Subscription granted (manual — not a payment).',
      d.user_id,
    );
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

  cancelSub(s: Subscription): void {
    if (this.actionReason.trim().length < 3) return this.warn('Enter a reason before cancelling.');
    if (!confirm(`Cancel "${s.plan_name}" for ${s.user_email}? Access ends now; history is kept. No refund is issued automatically.`)) return;
    this.run(this.api.cancel(s.id, this.actionReason.trim()), 'Subscription cancelled.', s.user_id);
  }

  saveCredits(s: Subscription): void {
    const value = Number(this.creditDraft[s.id]);
    if (!Number.isInteger(value) || value < 0) return this.warn('Enter a whole number of screenshots.');
    if (this.actionReason.trim().length < 3) return this.warn('Enter a reason before changing the allowance.');
    if (value < s.screenshots_used) return this.warn(`Allowance cannot be below the ${s.screenshots_used} already used.`);
    this.run(this.api.setCredits(s.id, value, this.actionReason.trim()), `Allowance set to ${value}.`, s.user_id);
  }

  // ------------------------------------------------- refunds & disputes
  refundReason = '';

  /** Full refund at the gateway (Cashfree) — the customer gets the money back and access ends. */
  refundPayment(p: PaymentRecord): void {
    if (this.refundReason.trim().length < 3) return this.warn('Enter a refund reason (it is stored in the audit log).');
    const amount = this.money(p.amount_minor, p.currency);
    if (!confirm(`Refund ${amount} to ${p.user_email} through ${p.gateway}? The money goes back to the customer and their access ends now. This cannot be undone.`)) return;
    this.busy.set(true);
    this.api.refund(p.id, this.refundReason.trim()).subscribe({
      next: (r) => {
        this.busy.set(false);
        this.refundReason = '';
        this.info.set(`Payment #${p.id}: ${r.message}`);
        this.openPayment(p);
        if (this.view === 'payments') this.loadPayments(this.payFilters.page ?? 1);
      },
      error: (e) => {
        this.busy.set(false);
        this.fail(e);
      },
    });
  }

  /** Download the evidence pack as a text file to upload at the gateway's dispute page. */
  downloadEvidence(p: PaymentRecord): void {
    this.api.evidence(p.id).subscribe({
      next: (d) => {
        const url = URL.createObjectURL(new Blob([d.summary_text], { type: 'text/plain;charset=utf-8' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = `evidence-${d.payment.order_id}.txt`;
        a.click();
        URL.revokeObjectURL(url);
      },
      error: (e) => this.fail(e),
    });
  }

  /** ok / warn / danger for the chargeback ratio (Cashfree's limit is 1% by value). */
  disputeLevel(o: BillingOverview): 'ok' | 'warn' | 'danger' {
    const rate = o.dispute_value_rate_inr ?? o.dispute_rate ?? 0;
    return rate >= 0.01 ? 'danger' : rate >= 0.005 ? 'warn' : 'ok';
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

  private run(obs: Observable<unknown>, ok: string, userId: number): void {
    this.busy.set(true);
    this.error.set('');
    obs.subscribe({
      next: () => {
        this.busy.set(false);
        this.info.set(ok);
        this.openUser(userId);
        if (this.subs()) this.loadSubs(this.subFilters.page ?? 1);
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
      id: null, code: '', name: '', description: '', price: 0, currency: 'INR', duration_value: 30,
      duration_unit: 'days', screenshot_allowance: 0, billing_type: 'one_time', billing_interval_days: null,
      trial_days: 0, promo_price: null, promo_label: '', promo_starts_at: '', promo_ends_at: '',
      features: '', badge: '', is_highlighted: false, is_active: true, is_public: true,
      sort_order: (this.plans().length + 1) * 10, reason: '',
    };
  }

  editPlan(p: AdminPlan): void {
    const unit = p.duration_unit === 'hours' || p.duration_hours % 24 !== 0 ? 'hours' : 'days';
    this.planForm = {
      id: p.id, code: p.code, name: p.name, description: p.description, price: p.regular_price_minor / 100,
      currency: p.currency, duration_value: unit === 'hours' ? p.duration_hours : p.duration_hours / 24,
      duration_unit: unit, screenshot_allowance: p.screenshot_allowance,
      billing_type: p.billing_type === 'recurring' ? 'recurring' : 'one_time',
      billing_interval_days: p.billing_interval_days, trial_days: p.trial_days,
      promo_price: p.promo_price_minor === null ? null : p.promo_price_minor / 100,
      promo_label: p.promo_label, promo_starts_at: localInput(p.promo_starts_at),
      promo_ends_at: localInput(p.promo_ends_at), features: p.features.join('\n'), badge: p.badge,
      is_highlighted: p.is_highlighted, is_active: p.is_active, is_public: p.is_public,
      sort_order: p.sort_order, reason: '',
    };
  }

  formHours(f: PlanForm): number {
    const v = Number(f.duration_value) || 0;
    return f.duration_unit === 'hours' ? v : v * 24;
  }

  savePlan(): void {
    const f = this.planForm;
    if (!f || this.busy()) return;
    const hours = this.formHours(f);
    if (!Number.isInteger(hours) || hours < 1) return this.warn('Duration must be at least 1 hour.');
    const hasPromo = f.promo_price !== null && `${f.promo_price}` !== '';
    const body: PlanInput = {
      name: f.name.trim(), description: f.description, price_minor: toMinor(f.price) ?? 0,
      currency: f.currency.trim().toUpperCase(), duration_hours: hours, duration_unit: f.duration_unit,
      screenshot_allowance: Number(f.screenshot_allowance), billing_type: f.billing_type,
      billing_interval_days: f.billing_type === 'recurring' ? Number(f.billing_interval_days) || null : null,
      trial_days: Number(f.trial_days), badge: f.badge.trim(), is_highlighted: f.is_highlighted,
      is_active: f.is_active, is_public: f.is_public, sort_order: Number(f.sort_order),
      features: f.features.split('\n').map((x) => x.trim()).filter(Boolean),
      reason: f.reason.trim() || undefined,
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

  togglePlan(p: AdminPlan, field: 'is_active' | 'is_public'): void {
    this.api.updatePlan(p.id, { [field]: !p[field] }).subscribe({
      next: () => this.loadPlans(),
      error: (e) => this.fail(e),
    });
  }

  /** The live price of a plan in a currency (or null when none is set). */
  currentPrice(p: AdminPlan, currency: string): PlanPrice | null {
    return p.prices.find((x) => x.currency === currency && x.is_current) ?? null;
  }

  editPrice(p: AdminPlan, currency: string): void {
    const cur = this.currentPrice(p, currency);
    this.priceForm = {
      plan: p, currency, amount: cur ? cur.amount_minor / 100 : null, is_active: cur?.is_active ?? true,
      effective_from: '', effective_to: localInput(cur?.effective_to ?? null),
      razorpay_plan_id: cur?.provider_refs?.['razorpay_plan_id'] ?? '', reason: '',
    };
  }

  savePrice(): void {
    const f = this.priceForm;
    if (!f || this.busy()) return;
    const minor = toMinor(f.amount === null ? null : Number(f.amount));
    if (minor === null || minor < 0) return this.warn('Enter a price (0 or more).');
    this.busy.set(true);
    this.api
      .setPrice(f.plan.id, {
        currency: f.currency, amount_minor: minor, is_active: f.is_active,
        effective_from: isoOrNull(f.effective_from), effective_to: isoOrNull(f.effective_to),
        provider_refs: f.razorpay_plan_id.trim() ? { razorpay_plan_id: f.razorpay_plan_id.trim() } : null,
        reason: f.reason.trim(),
      })
      .subscribe({
        next: () => {
          this.busy.set(false);
          this.info.set(`${f.currency} price of "${f.plan.name}" saved. Past payments are unchanged.`);
          this.priceForm = null;
          this.loadPlans();
          this.loadCurrencies();
        },
        error: (e) => {
          this.busy.set(false);
          this.fail(e);
        },
      });
  }

  showHistory(p: AdminPlan): void {
    this.api.priceHistory(p.id).subscribe({
      next: (rows) => this.priceHistory.set({ plan: p, rows }),
      error: (e) => this.fail(e),
    });
  }

  toggleCurrency(c: Currency, field: 'display_enabled' | 'payments_enabled'): void {
    const next = !c[field];
    if (field === 'payments_enabled' && next && c.live_prices === 0) {
      return this.warn(`Set ${c.code} prices on the plans before enabling ${c.code} payments.`);
    }
    if (field === 'payments_enabled' && next &&
        !confirm(`Enable ${c.code} payments? Make sure the gateway merchant account accepts ${c.code} first.`)) return;
    this.api.updateCurrency(c.code, { [field]: next, reason: this.currencyReason.trim() }).subscribe({
      next: (list) => {
        this.currencies.set(list);
        this.info.set(`${c.code} updated.`);
      },
      error: (e) => this.fail(e),
    });
  }

  // ---------------------------------------------- payments on/off + gateway
  /** Short label for the status chip and the settings card. */
  payStateLabel(ps: AdminPaymentSettings): string {
    if (!ps.payments_enabled) return 'Payments off';
    const on = this.enabledGateways(ps);
    return on.length ? `Payments on · ${on.map((g) => g.display_name).join(' + ')}` : 'No gateway on';
  }

  /** on = checkout open through a configured gateway; warn = something is on without keys. */
  payState(ps: AdminPaymentSettings): 'on' | 'warn' | 'off' {
    if (!ps.payments_enabled) return 'off';
    return ps.checkout_available && this.unconfiguredOn(ps).length === 0 ? 'on' : 'warn';
  }

  gatewayName(ps: AdminPaymentSettings, key: SelectableGateway): string {
    return ps.gateways.find((g) => g.key === key)?.display_name ?? key;
  }

  enabledGateways(ps: AdminPaymentSettings): AdminGatewayOption[] {
    return ps.gateways.filter((g) => g.enabled);
  }

  /** Gateways switched on whose keys are not set on the server (not offered at checkout yet). */
  unconfiguredOn(ps: AdminPaymentSettings): AdminGatewayOption[] {
    return ps.gateways.filter((g) => g.enabled && !g.configured);
  }

  togglePayments(ps: AdminPaymentSettings): void {
    const next = !ps.payments_enabled;
    if (!next && !confirm('Turn OFF online payments for the whole site? Customers will see "Payments are temporarily unavailable" and cannot start a checkout. Orders already paid still activate.')) return;
    this.savePaymentSettings({ payments_enabled: next }, next ? 'Online payments are ON.' : 'Online payments are OFF for the whole site.');
  }

  /** Switch one gateway on/off. Both may be on — the customer then picks one at checkout. */
  toggleGateway(ps: AdminPaymentSettings, g: AdminGatewayOption): void {
    if (this.busy()) return;
    const next = !g.enabled;
    if (!next && this.enabledGateways(ps).length === 1) {
      return this.warn('Keep at least one gateway on. To stop all payments, turn off site-wide online payments instead.');
    }
    if (!next && !confirm(`Turn OFF ${g.display_name} for new checkouts? Orders already started keep verifying through it.`)) return;
    this.savePaymentSettings(
      { gateways_enabled: { [g.key]: next } },
      next
        ? `${g.display_name} is ON${g.configured ? ' and offered at checkout' : ' — it is offered at checkout once its keys are set on the server'}.`
        : `${g.display_name} is OFF for new checkouts.`,
    );
  }

  /** The default gateway is pre-selected at checkout (switching it on if needed). */
  setDefaultGateway(ps: AdminPaymentSettings, g: AdminGatewayOption): void {
    if (g.is_default || this.busy()) return;
    this.savePaymentSettings({ active_gateway: g.key }, `${g.display_name} is now the default gateway at checkout.`);
  }

  private savePaymentSettings(body: PaymentSettingsUpdate, ok: string): void {
    this.busy.set(true);
    this.error.set('');
    this.api.updatePaymentSettings({ ...body, reason: this.paySettingsReason.trim() }).subscribe({
      next: (p) => {
        this.busy.set(false);
        this.paySettings.set(p);
        this.paySettingsReason = '';
        this.info.set(ok);
        this.loadCurrencies(); // "payable now" follows the switch
      },
      error: (e) => {
        this.busy.set(false);
        this.fail(e);
      },
    });
  }

  // -------------------------------------------------------------- misc
  readonly auditActions = [
    'purchase', 'grant', 'extend', 'revoke', 'cancel', 'refund', 'credits_adjust', 'trial_start',
    'plan_create', 'plan_update', 'price_set', 'currency_update', 'payment_settings', 'catalogue_migration',
  ];

  label(action: string): string {
    return action.split('_').join(' ');
  }

  previewMinor(f: PlanForm): number {
    return toMinor(Number(f.price) || 0) ?? 0;
  }

  statusEntries(o: BillingOverview): [string, number][] {
    return Object.entries(o.payments_by_status).sort((a, b) => b[1] - a[1]);
  }

  planEntries(o: BillingOverview): [string, number][] {
    return Object.entries(o.subscriptions_by_plan).sort((a, b) => b[1] - a[1]);
  }

  maxOf(entries: [string, number][]): number {
    return Math.max(1, ...entries.map((e) => e[1]));
  }

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
