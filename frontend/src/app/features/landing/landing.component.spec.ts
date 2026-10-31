import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { Plan } from '../../core/models/billing.models';
import { LandingComponent } from './landing.component';

const PLAN: Plan = {
  id: 3, code: '1-month', name: '1 Month', description: 'Coverage', currency: 'INR',
  duration_days: 30, duration_hours: 720, duration_unit: 'days', screenshot_allowance: 240,
  billing_type: 'recurring', billing_interval_days: 30, auto_renew_available: false, available: true, payable: true,
  trial_days: 0, price_minor: 90000, regular_price_minor: 90000,
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

  it('names the main search keyword in the page heading', () => {
    const h1 = render([PLAN]).querySelector('h1')?.textContent ?? '';
    expect(h1).toContain('AI interview assistant');
  });

  it('shows backend plan prices, not hard-coded ones', () => {
    const el = render([PLAN]);
    expect(el.querySelector('.plan.hl')?.textContent).toContain('900');
  });

  it('hides the meeting/tab audio, answer length and latest-models feature lines on home plan cards', () => {
    const el = render([{ ...PLAN, features: ['Resume-aware answers', 'Meeting / tab audio transcription', 'Answer length control (short / medium / long)', 'Coding questions', 'Latest AI models, chosen and updated by us'] }]);
    const items = [...el.querySelectorAll('.feats li')].map((li) => li.textContent?.trim());
    expect(items).toEqual(['Resume-aware answers', 'Coding questions']);
  });

  it('shows no screenshot wording on home plan cards', () => {
    const el = render([{ ...PLAN, description: '30-day access with 240 screenshot analyses. Suitable for regular Call preparation.' }]);
    expect(el.querySelector('.plan-desc')?.textContent).toContain('Suitable for regular Call preparation.');
    expect(el.querySelector('.plans')?.textContent).not.toMatch(/screenshot/i);
  });

  it('shows "(Unlimited Call Sessions)" on every plan card', () => {
    const el = render([PLAN, { ...PLAN, id: 2, is_highlighted: false }]);
    const tags = el.querySelectorAll('.plan [data-testid="plan-unlimited"]');
    expect(tags.length).toBe(2);
    expect(tags[0].textContent).toContain('(Unlimited Call Sessions)');
  });

  it('never says "free trial" when no plan has trial days', () => {
    const el = render([PLAN]);
    expect(el.textContent?.toLowerCase()).not.toContain('free trial');
    expect(el.querySelector('.hero-btn')?.textContent).toContain('Get started');
  });

  it('has a video tutorial section that links to the YouTube tutorial', () => {
    const section = render([PLAN]).querySelector('#tutorial');
    expect(section?.textContent).toContain('See everything in under 3 minutes');
    const link = section?.querySelector('a[href="https://youtu.be/TUZaStbmdag"]');
    expect(link?.getAttribute('target')).toBe('_blank');
    expect(link?.getAttribute('rel')).toContain('noopener');
  });

  it('shows our thumbnail and loads nothing from YouTube until Play', () => {
    const section = render([PLAN]).querySelector('#tutorial')!;
    expect(section.querySelector('.tut-facade img')?.getAttribute('src')).toBe('/tutorial-thumbnail.jpg');
    expect(section.querySelector('iframe')).toBeNull();
  });

  it('plays the tutorial in the privacy-enhanced player', () => {
    const section = render([PLAN]).querySelector('#tutorial')!;
    section.querySelector<HTMLButtonElement>('.tut-facade')!.click();
    const src = section.querySelector('iframe')?.getAttribute('src') ?? '';
    expect(src).toContain('https://www.youtube-nocookie.com/embed/TUZaStbmdag');
    expect(src).toContain('start=0');
    expect(section.querySelector('.tut-facade')).toBeNull();
  });

  it('starts the tutorial at the chapter that was picked', () => {
    const section = render([PLAN]).querySelector('#tutorial')!;
    const chip = [...section.querySelectorAll<HTMLButtonElement>('.tut-chip')].find((b) => b.textContent?.includes('Desktop app'))!;
    expect(chip.textContent).toContain('1:45');
    chip.click();
    expect(section.querySelector('iframe')?.getAttribute('src')).toContain('start=105');
  });

  it('offers the free trial only when a plan has trial_days > 0', () => {
    const el = render([{ ...PLAN, trial_days: 3 }]);
    expect(el.querySelector('.hero-btn')?.textContent).toContain('Start your free trial');
  });
});
