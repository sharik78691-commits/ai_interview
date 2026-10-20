import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AudioService } from '../../core/services/audio.service';
import { ResumeService } from '../../core/services/resume.service';
import { SettingsService } from '../../core/services/settings.service';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';
import { ResponseLength } from '../../core/models/interview.models';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [FormsModule, ErrorBannerComponent],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.css',
})
export class SettingsComponent implements OnInit {
  private settings = inject(SettingsService);
  private resumeService = inject(ResumeService);
  audio = inject(AudioService);

  aiModel = 'auto';
  /**
   * Display-only choices. The pick is saved locally but never sent: the
   * backend always answers with the model the admin set as default.
   */
  readonly aiModelList = [
    { key: 'auto', displayName: 'Auto (Recommended)', hint: 'Best balance of speed and quality.' },
    { key: 'fast', displayName: 'Fast', hint: 'Quickest first words.' },
    { key: 'balanced', displayName: 'Balanced', hint: 'Fast with richer detail.' },
    { key: 'advanced', displayName: 'Advanced', hint: 'Deeper reasoning for hard questions.' },
  ];
  sttProvider = 'browser';
  responseLength: ResponseLength = 'default';
  theme = 'dark';
  micId = '';
  mics: MediaDeviceInfo[] = [];
  health = 'checking…';
  error = '';
  saved = false;

  ngOnInit(): void {
    const s = this.settings.value;
    this.aiModel = s.aiModel;
    this.sttProvider = s.sttProvider;
    this.responseLength = s.responseLength;
    this.theme = s.theme;
    this.micId = s.micId;
    this.audio.listMics().then((m) => (this.mics = m));
    // A key saved before the list became display-only falls back to Auto.
    if (!this.aiModelList.some((m) => m.key === this.aiModel)) this.aiModel = 'auto';
    this.resumeService.checkHealth().subscribe({
      next: (h) => {
        const mode = h.demo_mode ? 'demo' : h.llm_configured ? 'live AI' : 'online';
        this.health = `online · ${mode}`;
      },
      error: () => (this.health = 'offline — start backend at http://localhost:8000'),
    });
  }

  /** Hint line for the selected display-only model. */
  aiModelHint(): string {
    return this.aiModelList.find((m) => m.key === this.aiModel)?.hint ?? '';
  }

  save(): void {
    this.settings.save({ aiModel: this.aiModel, sttProvider: this.sttProvider, responseLength: this.responseLength, theme: this.theme, micId: this.micId });
    this.saved = true;
    setTimeout(() => (this.saved = false), 2000);
  }

  reset(): void {
    this.settings.reset();
    const s = this.settings.value;
    this.aiModel = s.aiModel;
    this.sttProvider = s.sttProvider;
    this.responseLength = s.responseLength;
    this.theme = s.theme;
    this.micId = s.micId;
  }
}
