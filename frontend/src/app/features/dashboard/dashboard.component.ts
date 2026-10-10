import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ResumeData } from '../../core/models/interview.models';
import { ResumeService } from '../../core/services/resume.service';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';
import { environment } from '../../../environments/environment';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [FormsModule, ErrorBannerComponent],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.css',
})
export class DashboardComponent {
  private resumeService = inject(ResumeService);
  private router = inject(Router);
  /** Desktop installer links + version for the download card (empty URL = coming soon). */
  readonly desktopApp = environment.desktopApp;

  resumeText = this.resumeService.resumeText;
  jobDescription = this.resumeService.jobDescription;
  targetPosition = '';
  resumeData: ResumeData | null = this.resumeService.storedResumeData;
  error = '';
  uploading = false;
  preparing = false;
  dragOver = false;

  onFilePicked(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    if (input.files?.length) this.upload(input.files[0]);
  }

  onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.dragOver = false;
    if (ev.dataTransfer?.files.length) this.upload(ev.dataTransfer.files[0]);
  }

  upload(file: File): void {
    this.error = '';
    this.uploading = true;
    this.resumeService.uploadResume(file).subscribe({
      next: (d) => {
        this.uploading = false;
        this.resumeData = d;
        this.resumeText = d.rawText;
      },
      error: () => {
        this.uploading = false;
        this.error = 'Upload failed — is the backend running at http://localhost:8000? You can also paste resume text manually below.';
      },
    });
  }

  prepare(): void {
    this.error = '';
    // Resume and target role are OPTIONAL — the user can start an interview
    // directly without providing either.
    this.preparing = true;
    this.resumeService.prepareInterview(this.resumeText.trim(), this.jobDescription.trim()).subscribe({
      next: () => {
        this.preparing = false;
        this.router.navigate(['/interview']);
      },
      error: () => {
        this.preparing = false;
        // Backend down: persist locally and still enter interview (HTTP fallback / demo guidance later).
        this.resumeService.saveResumeText(this.resumeText.trim());
        this.resumeService.saveJobDescription(this.jobDescription.trim());
        this.router.navigate(['/interview']);
      },
    });
  }
}
