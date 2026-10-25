import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { AdminPaymentSettings, BillingOverview } from '../../../core/models/billing.models';
import { AdminBillingService } from '../../../core/services/admin-billing.service';
import { AdminBillingComponent } from './admin-billing.component';

const SETTINGS: AdminPaymentSettings = {
  payments_enabled: true,
  active_gateway: 'cashfree',
  checkout_available: true,
  updated_at: null,
  updated_by_user_id: null,
  gateways: [
    { key: 'cashfree', display_name: 'Cashfree', enabled: true, is_default: true, configured: true, currencies: ['INR'] },
    { key: 'razorpay', display_name: 'Razorpay', enabled: false, is_default: false, configured: true, currencies: ['INR'] },
  ],
};

describe('AdminBillingComponent (online payments & gateway)', () => {
  let api: jasmine.SpyObj<AdminBillingService>;

  beforeEach(() => {
    api = jasmine.createSpyObj<AdminBillingService>('AdminBillingService', [
      'plans', 'currencies', 'overview', 'paymentSettings', 'updatePaymentSettings',
    ]);
    api.plans.and.returnValue(of([]));
    api.currencies.and.returnValue(of([]));
    api.overview.and.returnValue(of(null as unknown as BillingOverview));
    api.paymentSettings.and.returnValue(of(SETTINGS));
    TestBed.configureTestingModule({
      imports: [AdminBillingComponent],
      providers: [{ provide: AdminBillingService, useValue: api }],
    });
  });

  function render() {
    const fixture = TestBed.createComponent(AdminBillingComponent);
    fixture.detectChanges();
    fixture.componentInstance.setView('plans');
    fixture.detectChanges();
    return fixture;
  }

  const siteSwitch = (el: HTMLElement) => el.querySelector('.ps-switch-row .switch') as HTMLButtonElement;
  const gwSwitch = (el: HTMLElement, i: number) => el.querySelectorAll('.gw-card .switch')[i] as HTMLButtonElement;

  it('shows the site switch and both gateways, each with its own switch and the default marked', () => {
    const el: HTMLElement = render().nativeElement;
    expect(siteSwitch(el).getAttribute('aria-checked')).toBe('true');
    const cards = [...el.querySelectorAll('.gw-card')] as HTMLElement[];
    expect(cards.map((c) => c.querySelector('strong')?.textContent?.trim())).toEqual(['Cashfree', 'Razorpay']);
    expect(gwSwitch(el, 0).getAttribute('aria-checked')).toBe('true');
    expect(gwSwitch(el, 1).getAttribute('aria-checked')).toBe('false');
    expect(gwSwitch(el, 1).disabled).toBeFalse();
    expect(cards[0].textContent).toContain('Default');
    expect(cards[1].textContent).toContain('Make default');
    expect(el.querySelector('.pay-chip')?.textContent).toContain('Payments on · Cashfree');
  });

  it('turns payments off after confirmation and shows the new state', () => {
    spyOn(window, 'confirm').and.returnValue(true);
    api.updatePaymentSettings.and.returnValue(of({ ...SETTINGS, payments_enabled: false, checkout_available: false }));
    const fixture = render();
    siteSwitch(fixture.nativeElement).click();
    fixture.detectChanges();
    expect(api.updatePaymentSettings).toHaveBeenCalledWith({ payments_enabled: false, reason: '' });
    expect(fixture.nativeElement.querySelector('.pay-chip').textContent).toContain('Payments off');
  });

  it('does nothing when the admin cancels turning payments off', () => {
    spyOn(window, 'confirm').and.returnValue(false);
    const fixture = render();
    siteSwitch(fixture.nativeElement).click();
    expect(api.updatePaymentSettings).not.toHaveBeenCalled();
  });

  it('switches Razorpay on alongside Cashfree', () => {
    api.updatePaymentSettings.and.returnValue(of({
      ...SETTINGS, gateways: [SETTINGS.gateways[0], { ...SETTINGS.gateways[1], enabled: true }],
    }));
    const fixture = render();
    gwSwitch(fixture.nativeElement, 1).click();
    fixture.detectChanges();
    expect(api.updatePaymentSettings).toHaveBeenCalledWith({ gateways_enabled: { razorpay: true }, reason: '' });
    expect(fixture.nativeElement.querySelector('.pay-chip').textContent).toContain('Cashfree + Razorpay');
  });

  it('makes Razorpay the default', () => {
    api.updatePaymentSettings.and.returnValue(of({ ...SETTINGS, active_gateway: 'razorpay' }));
    const fixture = render();
    const make = fixture.nativeElement.querySelectorAll('.gw-card')[1].querySelector('.link') as HTMLButtonElement;
    make.click();
    expect(api.updatePaymentSettings).toHaveBeenCalledWith({ active_gateway: 'razorpay', reason: '' });
  });

  it('lets Razorpay be switched on without keys and warns it is not offered yet', () => {
    const unconfigured = {
      ...SETTINGS,
      gateways: [SETTINGS.gateways[0], { ...SETTINGS.gateways[1], enabled: true, configured: false }],
    };
    api.paymentSettings.and.returnValue(of(unconfigured));
    const el: HTMLElement = render().nativeElement;
    expect(gwSwitch(el, 1).disabled).toBeFalse();
    expect(el.querySelectorAll('.gw-card')[1].textContent).toContain('Keys not set');
    expect(el.querySelector('.pay-settings .banner')?.textContent).toContain('Razorpay');
  });

  it('refuses to switch off the only gateway that is on', () => {
    const fixture = render();
    gwSwitch(fixture.nativeElement, 0).click();
    expect(api.updatePaymentSettings).not.toHaveBeenCalled();
    expect(fixture.componentInstance.error()).toContain('at least one gateway');
  });
});
