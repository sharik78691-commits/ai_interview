import { firstValueFrom, of } from 'rxjs';
import { routes } from '../../app.routes';
import { SelectivePreloadingStrategy } from './selective-preloading.strategy';

describe('SelectivePreloadingStrategy', () => {
  const strategy = new SelectivePreloadingStrategy();

  it('preloads routes flagged data.preload', async () => {
    const load = jasmine.createSpy('load').and.returnValue(of('chunk'));
    expect(await firstValueFrom(strategy.preload({ path: 'x', data: { preload: true } }, load))).toBe(
      'chunk',
    );
    expect(load).toHaveBeenCalled();
  });

  it('skips routes without the flag', async () => {
    const load = jasmine.createSpy('load').and.returnValue(of('chunk'));
    expect(await firstValueFrom(strategy.preload({ path: 'admin' }, load))).toBeNull();
    expect(load).not.toHaveBeenCalled();
  });

  it('flags only the core signed-in flow for preloading', () => {
    const preloaded = routes.filter((r) => r.data?.['preload'] === true).map((r) => r.path);
    expect(preloaded.sort()).toEqual(['dashboard', 'interview']);
    for (const r of routes.filter((x) => x.path === 'dashboard' || x.path === 'interview')) {
      expect(r.loadComponent).toBeDefined();
      expect(r.component).toBeUndefined();
    }
  });
});
