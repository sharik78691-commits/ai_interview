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
  sttProvider = 'browser';
  responseLength: ResponseLength = 'medium';
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
