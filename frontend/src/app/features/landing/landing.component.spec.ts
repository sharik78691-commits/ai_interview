import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Plan } from '../../core/models/billing.models';
import { LandingComponent } from './landing.component';

const PLAN: Plan = {
  id: 3, code: '1-month', name: '1 Month', description: 'Coverage', currency: 'INR',
  duration_days: 30, trial_days: 0, price_minor: 90000, regular_price_minor: 90000,
  is_promo: false, promo_label: '', promo_ends_at: null, features: ['Real-time AI answer guidance'],
  badge: 'Most popular', is_highlighted: true,
};

describe('LandingComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [LandingComponent],
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(plans: Plan[]): HTMLElement {
    const fixture = TestBed.createComponent(LandingComponent);
    fixture.detectChanges();
    http.expectOne((r) => r.url.endsWith('/api/plans')).flush(plans);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows backend plan prices, not hard-coded ones', () => {
    const el = render([PLAN]);
    expect(el.querySelector('.plan.hl')?.textContent).toContain('900');
  });

  it('never says "free trial" when no plan has trial days', () => {
    const el = render([PLAN]);
    expect(el.textContent?.toLowerCase()).not.toContain('free trial');
    expect(el.querySelector('.hero-btn')?.textContent).toContain('Get started');
  });

  it('offers the free trial only when a plan has trial_days > 0', () => {
    const el = render([{ ...PLAN, trial_days: 3 }]);
    expect(el.querySelector('.hero-btn')?.textContent).toContain('Start your free trial');
  });
});
