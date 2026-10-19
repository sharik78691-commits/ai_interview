import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AudioService } from '../../core/services/audio.service';
import { ResumeService } from '../../core/services/resume.service';
import { SettingsService } from '../../core/services/settings.service';
import { AiModelsService } from '../../core/services/ai-models.service';
import { AiModel } from '../../core/models/ai-models';
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
  private aiModels = inject(AiModelsService);
  audio = inject(AudioService);

  aiModel = 'auto';
  /** Model catalog from the backend (never hardcoded). */
  aiModelList: AiModel[] = [];
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
    // Model list comes from the backend allowlist (names + availability only).
    this.aiModels.listModels().subscribe((models) => {
      this.aiModelList = models;
      // Keep a stale saved key from pointing at a model that no longer exists.
      if (this.aiModel !== 'auto' && !models.some((m) => m.key === this.aiModel)) {
        this.aiModel = models.find((m) => m.isDefault)?.key ?? 'auto';
      }
    });
    this.resumeService.checkHealth().subscribe({
      next: (h) => {
        const mode = h.demo_mode ? 'demo' : h.llm_configured ? 'live AI' : 'online';
        this.health = `online · ${mode}${h.model ? ' · ' + h.model : ''}`;
      },
      error: () => (this.health = 'offline — start backend at http://localhost:8000'),
    });
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
