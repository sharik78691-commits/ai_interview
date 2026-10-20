import { DatePipe } from '@angular/common';
import { Component, ElementRef, OnDestroy, OnInit, computed, inject, signal, viewChild } from '@angular/core';
import { AnalysisHistoryService } from '../../core/screenshot/analysis-history.service';
import { validateImages } from '../../core/screenshot/image-validation';
import { ModelConfigService } from '../../core/screenshot/model-config.service';
import { ScreenCaptureService } from '../../core/screenshot/screen-capture.service';
import { ScreenshotAnalysisService } from '../../core/screenshot/screenshot-analysis.service';
import {
  AnalysisResult,
  CaptureSource,
  CapturedImage,
  HistoryEntry,
  ScreenshotError,
} from '../../core/screenshot/screenshot.models';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';
import { AnalysisResultComponent } from './analysis-result.component';

export interface ShotItem {
  id: number;
  image: CapturedImage;
}

type PickerMode = { kind: 'add' } | { kind: 'replace'; index: number };

/**
 * Desktop-only: capture one or more screenshots of interview questions and get
 * answer guidance. Reachable only through `desktopOnlyGuard`.
 */
@Component({
  selector: 'app-screenshot-analysis',
  standalone: true,
  imports: [DatePipe, ErrorBannerComponent, AnalysisResultComponent],
  templateUrl: './screenshot-analysis.component.html',
  styleUrl: './screenshot-analysis.component.css',
})
export class ScreenshotAnalysisComponent implements OnInit, OnDestroy {
  private capture = inject(ScreenCaptureService);
  private analysis = inject(ScreenshotAnalysisService);
  readonly config = inject(ModelConfigService);
  readonly history = inject(AnalysisHistoryService);

  private nextId = 1;
  private timer: ReturnType<typeof setInterval> | null = null;
  private disableHotkey: (() => void) | null = null;
  private destroyed = false;

  readonly shots = signal<ShotItem[]>([]);
  readonly picker = signal<PickerMode | null>(null);
  readonly sources = signal<CaptureSource[]>([]);
  /** Keyboard-highlighted source in the picker (arrow keys move it, Enter captures). */
  readonly selectedIndex = signal(0);
  private readonly pickerModal = viewChild<ElementRef<HTMLElement>>('pickerModal');
  readonly loadingSources = signal(false);
  readonly capturing = signal(false);
  readonly captureNotice = signal('');
  readonly analyzing = signal(false);
  readonly elapsed = signal(0);
  readonly error = signal('');
  readonly result = signal<AnalysisResult | null>(null);
  readonly resultModel = signal('');

  readonly validation = computed(() =>
    this.shots().length
      ? validateImages(
          this.shots().map((s) => s.image),
          this.config.activeModel().limits,
        )
      : null,
  );
  readonly canAnalyze = computed(
    () =>
      !this.analyzing() &&
      this.config.canUseServer() &&
      this.shots().length > 0 &&
      (this.validation()?.ok ?? false),
  );
  readonly atLimit = computed(() => this.shots().length >= this.config.activeModel().limits.maxImages);
  readonly totalMb = computed(() => ((this.validation()?.totalBytes ?? 0) / (1024 * 1024)).toFixed(1));

  async ngOnInit(): Promise<void> {
    // Ctrl+Shift+S on this page opens the picker (elsewhere it navigates here).
    this.capture
      .enableHotkey((sourceId, err) => this.onHotkey(sourceId, err))
      .then((off) => {
        if (this.destroyed) off();
        else this.disableHotkey = off;
      });
    // Opened by Ctrl+Shift+S from another page: show the picker right away, so
    // one press + Enter captures and analyzes (no second press needed).
    const pending = this.capture.takePendingPick();
    if (pending) void this.onHotkey(pending, null);
    try {
      await this.config.load();
    } catch (e) {
      this.error.set(this.message(e));
    }
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.disableHotkey?.();
    this.disableHotkey = null;
    this.stopTimer();
  }

  dataUrl(img: CapturedImage): string {
    return `data:${img.mimeType};base64,${img.data}`;
  }

  // ---- capture (always user-initiated: open picker → user chooses a source) ----

  async openPicker(mode: PickerMode, preselectId?: string): Promise<void> {
    this.error.set('');
    this.picker.set(mode);
    this.selectedIndex.set(0);
    this.loadingSources.set(true);
    try {
      const list = await this.capture.listSources();
      this.sources.set(list);
      const idx = preselectId ? list.findIndex((s) => s.id === preselectId) : -1;
      this.selectedIndex.set(Math.max(0, idx));
    } catch (e) {
      this.picker.set(null);
      this.error.set(this.message(e));
    } finally {
      this.loadingSources.set(false);
    }
    // Focus the dialog so arrow / Enter keys reach it right away.
    setTimeout(() => this.pickerModal()?.nativeElement.focus());
  }

  /** Picker keyboard: arrows move the highlight, Enter captures + analyzes at once, Esc cancels. */
  onPickerKey(event: KeyboardEvent): void {
    const count = this.sources().length;
    switch (event.key) {
      case 'ArrowRight':
        this.moveSelection(1);
        break;
      case 'ArrowLeft':
        this.moveSelection(-1);
        break;
      case 'ArrowDown':
        this.moveSelection(this.pickerColumns());
        break;
      case 'ArrowUp':
        this.moveSelection(-this.pickerColumns());
        break;
      case 'Enter': {
        const source = this.sources()[this.selectedIndex()];
        if (source && !this.capturing() && !this.loadingSources()) {
          void this.takeCapture(source, { analyzeNow: true });
        }
        break;
      }
      case 'Escape':
        this.closePicker();
        break;
      default:
        return;
    }
    event.preventDefault();
    event.stopPropagation();
  }

  /**
   * Move the highlight by `step` items. Up/Down step a whole grid row; when
   * that would leave the grid (e.g. a single row of screens) they move to the
   * previous/next item instead, wrapping around, so every arrow always moves.
   */
  private moveSelection(step: number): void {
    const count = this.sources().length;
    if (!count) return;
    const current = this.selectedIndex();
    let next = current + step;
    if (next < 0 || next >= count || step === 0) next = current + Math.sign(step || 1);
    this.selectedIndex.set(((next % count) + count) % count);
    setTimeout(() =>
      this.pickerModal()
        ?.nativeElement.querySelectorAll<HTMLElement>('.source')
        [this.selectedIndex()]?.scrollIntoView?.({ block: 'nearest' }),
    );
  }

  /** Number of source tiles per row in the picker grid (from the rendered layout). */
  private pickerColumns(): number {
    const tiles = this.pickerModal()?.nativeElement.querySelectorAll<HTMLElement>('.source');
    if (!tiles?.length) return 1;
    const top = tiles[0].offsetTop;
    let cols = 0;
    while (cols < tiles.length && tiles[cols].offsetTop === top) cols++;
    return Math.max(1, cols);
  }

  closePicker(): void {
    if (!this.capturing()) this.picker.set(null);
  }

  /**
   * Capture the chosen source. With `analyzeNow` (the Enter key) the analysis
   * starts right after a successful capture — no extra click on "Analyze".
   * A mouse click only captures, so several screenshots can be collected first.
   */
  async takeCapture(source: CaptureSource, opts: { analyzeNow?: boolean } = {}): Promise<void> {
    const mode = this.picker();
    if (!mode) return;
    const captured = await this.captureInto(source.id, mode);
    if (captured && opts.analyzeNow) await this.analyze();
  }

  /** Ctrl+Shift+S: open the picker with the screen under the cursor preselected. */
  async onHotkey(sourceId: string | null, err: ScreenshotError | null): Promise<void> {
    if (err || !sourceId) {
      this.error.set(err ? this.message(err) : 'Could not find a screen to capture.');
      return;
    }
    if (this.analyzing() || this.atLimit()) {
      this.error.set(
        this.atLimit()
          ? 'Screenshot limit reached — remove one before capturing another.'
          : 'Wait for the current analysis to finish before capturing.',
      );
      return;
    }
    if (this.picker()) return;
    await this.openPicker({ kind: 'add' }, sourceId);
  }

  /** True when a screenshot was captured and added. */
  private async captureInto(sourceId: string, mode: PickerMode): Promise<boolean> {
    if (this.capturing()) return false;
    this.capturing.set(true);
    this.error.set('');
    try {
      const image = await this.capture.capture(sourceId);
      this.shots.update((list) => {
        if (mode.kind === 'replace' && list[mode.index]) {
          return list.map((s, i) => (i === mode.index ? { id: s.id, image } : s));
        }
        return [...list, { id: this.nextId++, image }];
      });
      this.captureNotice.set(`Captured “${image.sourceName}” (${image.width}×${image.height})`);
      setTimeout(() => this.captureNotice.set(''), 3000);
      this.picker.set(null);
      return true;
    } catch (e) {
      this.error.set(this.message(e));
      return false;
    } finally {
      this.capturing.set(false);
    }
  }

  // ---- screenshot list controls ----

  remove(index: number): void {
    this.shots.update((list) => list.filter((_, i) => i !== index));
  }

  move(index: number, delta: -1 | 1): void {
    const to = index + delta;
    this.shots.update((list) => {
      if (to < 0 || to >= list.length) return list;
      const next = [...list];
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });
  }

  clearAll(): void {
    this.shots.set([]);
  }

  // ---- analysis ----

  async analyze(): Promise<void> {
    if (!this.canAnalyze()) return;
    this.error.set('');
    this.result.set(null);
    this.analyzing.set(true);
    this.elapsed.set(0);
    this.timer = setInterval(() => this.elapsed.update((n) => n + 1), 1000);
    try {
      const images = this.shots().map((s) => s.image);
      const { result, model } = await this.analysis.analyze(images);
      this.result.set(result);
      this.resultModel.set(model);
      this.history.add(result, model, images.length);
    } catch (e) {
      this.error.set(this.message(e));
    } finally {
      this.stopTimer();
      this.analyzing.set(false);
    }
  }

  openHistory(entry: HistoryEntry): void {
    this.result.set(entry.result);
    this.resultModel.set(entry.model);
  }

  private stopTimer(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  private message(e: unknown): string {
    return e instanceof ScreenshotError ? e.message : 'Something went wrong. Please try again.';
  }
}
