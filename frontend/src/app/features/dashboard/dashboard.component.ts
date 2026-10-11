import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ResumeData } from '../../core/models/interview.models';
import { ResumeService, StoredResume } from '../../core/services/resume.service';
import { TargetRole, TargetRoleDetail, TargetRoleService } from '../../core/services/target-role.service';
import { ErrorBannerComponent } from '../../shared/components/error-banner/error-banner.component';
import { environment } from '../../../environments/environment';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [FormsModule, ErrorBannerComponent],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.css',
})
export class DashboardComponent implements OnInit {
  private resumeService = inject(ResumeService);
  private roleService = inject(TargetRoleService);
  private router = inject(Router);
  /** Desktop installer links + version for the download card (empty URL = coming soon). */
  readonly desktopApp = environment.desktopApp;

  resumeText = this.resumeService.resumeText;
  jobDescription = this.resumeService.jobDescription;
  targetPosition = this.roleService.localPosition;
  resumeData: ResumeData | null = this.resumeService.storedResumeData;
  error = '';
  uploading = false;
  preparing = false;
  dragOver = false;
  /** True after the first Prepare/Save attempt — drives inline field errors. */
  formSubmitted = false;

  /** Position error text (empty when valid or untouched). */
  get positionError(): string {
    if (!this.formSubmitted) return '';
    if (!this.targetPosition.trim()) return 'Position is required.';
    if (this.targetPosition.length > 200) {
      return 'Position must be 200 characters or less.';
    }
    return '';
  }

  /** Job-description error text (empty when valid or untouched). */
  get jdError(): string {
    if (!this.formSubmitted) return '';
    if (!this.jobDescription.trim()) return 'Job description is required.';
    if (this.jobDescription.length > 2000) {
      return 'Job description must be 2000 characters or less.';
    }
    return '';
  }

  /** Target-role section is mandatory: both fields, within their caps. */
  private validateRole(): boolean {
    this.formSubmitted = true;
    return !this.positionError && !this.jdError;
  }

  /** Server-side resume library (false = backend too old / unreachable). */
  serverOk = true;
  serverResumes: StoredResume[] = [];
  resumesLoading = true;
  actingResumeId: number | null = null;
  /** Stored row backing the editor (null = fresh/typed text, savable as new). */
  editingResumeId: number | null = null;
  savingResumeText = false;

  /** Server-side target-role library. */
  serverRoles: TargetRole[] = [];
  rolesLoading = true;
  actingRoleId: number | null = null;
  savingRole = false;

  ngOnInit(): void {
    // Fresh browser / new device: pull the account's active resume + role in.
    this.resumeService.adoptActiveResume().subscribe((detail) => {
      if (detail) {
        this.resumeText = this.resumeService.resumeText;
        this.resumeData = this.resumeService.storedResumeData;
        this.setEditing(detail.id);
      }
      this.roleService.adoptActiveRole().subscribe((role) => {
        if (role) {
          this.targetPosition = role.position || '';
          this.jobDescription = role.job_description || '';
        }
        this.loadLibrary();
        this.loadRoles();
        this.restoreEditingLink();
      });
    });
  }

  /** Remember which stored row the editor is linked to (persists reloads). */
  private setEditing(id: number | null): void {
    this.editingResumeId = id;
    this.resumeService.saveEditingId(id);
  }

  /**
   * After a reload the editor text survives (localStorage) but the link does
   * not — re-link only when the stored copy still matches the editor text.
   */
  private restoreEditingLink(): void {
    const id = this.resumeService.storedEditingId;
    if (id === null || !this.resumeText.trim()) return;
    this.resumeService.getResume(id).subscribe({
      next: (d) => {
        if ((d.raw_text || '') === this.resumeText) this.setEditing(id);
        else this.setEditing(null);
      },
      error: () => this.setEditing(null),
    });
  }

  /** Refresh the stored-resume list; hides the section when unsupported. */
  loadLibrary(): void {
    this.resumesLoading = true;
    this.resumeService.listResumes().subscribe({
      next: (rows) => {
        this.serverResumes = rows;
        this.resumesLoading = false;
      },
      error: () => {
        this.serverOk = false;
        this.resumesLoading = false;
      },
    });
  }

  /** Use a stored resume: make it active and load it into the editor. */
  useResume(row: StoredResume): void {
    this.error = '';
    this.actingResumeId = row.id;
    this.resumeService.selectResume(row.id).subscribe({
      next: (detail) => {
        this.actingResumeId = null;
        this.resumeService.applyDetail(detail);
        this.resumeText = this.resumeService.resumeText;
        this.resumeData = this.resumeService.storedResumeData;
        this.setEditing(detail.id);
        this.loadLibrary();
      },
      error: () => {
        this.actingResumeId = null;
        this.error = 'Could not load that resume. Please try again.';
      },
    });
  }

  /** Load a stored resume into the editor WITHOUT changing the active one. */
  editResume(row: StoredResume): void {
    this.error = '';
    this.actingResumeId = row.id;
    this.resumeService.getResume(row.id).subscribe({
      next: (detail) => {
        this.actingResumeId = null;
        this.resumeService.applyDetail(detail);
        this.resumeText = this.resumeService.resumeText;
        this.resumeData = this.resumeService.storedResumeData;
        this.setEditing(detail.id);
      },
      error: () => {
        this.actingResumeId = null;
        this.error = 'Could not load that resume for editing. Please try again.';
      },
    });
  }

  /** Delete a stored resume (server promotes the newest remaining one). */
  removeResume(row: StoredResume): void {
    if (!window.confirm(`Delete "${row.filename}" from your resumes?`)) return;
    this.error = '';
    this.actingResumeId = row.id;
    this.resumeService.deleteResume(row.id).subscribe({
      next: () => {
        this.actingResumeId = null;
        if (this.editingResumeId === row.id) this.setEditing(null);
        this.loadLibrary();
      },
      error: () => {
        this.actingResumeId = null;
        this.error = 'Could not delete that resume. Please try again.';
      },
    });
  }

  /** Save editor corrections onto the backing stored resume. */
  saveResumeEdits(): void {
    if (this.editingResumeId === null) return;
    if (!this.resumeText.trim()) {
      this.error = 'Resume text is empty — nothing to save.';
      return;
    }
    this.error = '';
    this.savingResumeText = true;
    this.resumeService.updateResumeText(this.editingResumeId, this.resumeText).subscribe({
      next: (detail) => {
        this.savingResumeText = false;
        this.resumeService.applyDetail(detail);
        this.resumeText = this.resumeService.resumeText;
        this.resumeData = this.resumeService.storedResumeData;
        this.loadLibrary();
      },
      error: () => {
        this.savingResumeText = false;
        this.error = 'Could not save changes. Please try again.';
      },
    });
  }

  /** Store fresh/typed editor text as a new resume (becomes active). */
  saveResumeAsNew(): void {
    if (!this.resumeText.trim()) {
      this.error = 'Paste or type resume text first.';
      return;
    }
    this.error = '';
    this.savingResumeText = true;
    const filename = `Pasted resume - ${new Date().toLocaleDateString()}.txt`;
    this.resumeService.createFromText(filename, this.resumeText).subscribe({
      next: (detail) => {
        this.savingResumeText = false;
        this.resumeService.applyDetail(detail);
        this.resumeText = this.resumeService.resumeText;
        this.resumeData = this.resumeService.storedResumeData;
        this.setEditing(detail.id);
        this.loadLibrary();
      },
      error: () => {
        this.savingResumeText = false;
        this.error = 'Could not save that resume. Please try again.';
      },
    });
  }

  /** Refresh the saved target-role list. */
  loadRoles(): void {
    this.rolesLoading = true;
    this.roleService.listRoles().subscribe({
      next: (rows) => {
        this.serverRoles = rows;
        this.rolesLoading = false;
      },
      error: () => {
        this.serverOk = false;
        this.rolesLoading = false;
      },
    });
  }

  /** Save the current position + JD as a role (becomes the active one). */
  saveRole(): void {
    this.error = '';
    if (!this.validateRole()) return;
    this.savingRole = true;
    // Same dedupe as Prepare: re-saving unchanged text is a no-op.
    this.ensureRoleSaved(
      this.targetPosition.trim(),
      this.jobDescription.trim(),
      (created) => {
        this.savingRole = false;
        if (created) {
          this.targetPosition = created.position || '';
          this.jobDescription = created.job_description || '';
          this.roleService.saveLocal(this.targetPosition, this.jobDescription);
        }
        this.loadRoles();
      },
      () => {
        this.savingRole = false;
        this.error = 'Could not save that role. Please try again.';
      },
    );
  }

  /**
   * Insert the role unless the active row already holds the exact same text.
   * Always finishes via `done(created | null)`; `failed()` only when the
   * library itself is unreachable. Never blocks the caller on errors.
   */
  private ensureRoleSaved(
    pos: string,
    jd: string,
    done: (created: TargetRoleDetail | null) => void,
    failed?: () => void,
  ): void {
    const skip = () => done(null);
    if (!this.serverOk) {
      (failed ?? skip)();
      return;
    }
    const active = this.serverRoles.find((r) => r.is_active);
    const insert = () => {
      this.roleService.createRole(pos, jd).subscribe({
        next: (detail) => done(detail),
        error: () => (failed ?? skip)(),
      });
    };
    if (!active || (active.position || '') !== pos) {
      insert();
      return;
    }
    // Same title — confirm the description matches before skipping.
    this.roleService.getRole(active.id).subscribe({
      next: (detail) => {
        if ((detail.job_description || '') === jd) done(null);
        else insert();
      },
      error: () => insert(),
    });
  }

  /** Use a saved role: make it active and load it into the inputs. */
  useRole(row: TargetRole): void {
    this.error = '';
    this.actingRoleId = row.id;
    this.roleService.selectRole(row.id).subscribe({
      next: (detail) => {
        this.actingRoleId = null;
        this.targetPosition = detail.position || '';
        this.jobDescription = detail.job_description || '';
        this.roleService.saveLocal(this.targetPosition, this.jobDescription);
        this.loadRoles();
      },
      error: () => {
        this.actingRoleId = null;
        this.error = 'Could not load that role. Please try again.';
      },
    });
  }

  /** Delete a saved role (server promotes the newest remaining one). */
  removeRole(row: TargetRole): void {
    const label = row.position || 'this role';
    if (!window.confirm(`Delete "${label}" from your target roles?`)) return;
    this.error = '';
    this.actingRoleId = row.id;
    this.roleService.deleteRole(row.id).subscribe({
      next: () => {
        this.actingRoleId = null;
        this.loadRoles();
      },
      error: () => {
        this.actingRoleId = null;
        this.error = 'Could not delete that role. Please try again.';
      },
    });
  }

  downloadHref(id: number): string {
    return this.resumeService.downloadUrl(id);
  }

  fmtDate(iso: string | null): string {
    if (!iso) return '';
    const d = new Date(iso);
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString();
  }

  formatSize(bytes: number): string {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

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
        // Server stored it (and made it active) — track the row, refresh list.
        this.setEditing(this.resumeService.lastStoredId);
        this.loadLibrary();
      },
      error: () => {
        this.uploading = false;
        this.error = 'Upload failed — is the backend running at http://localhost:8000? You can also paste resume text manually below.';
      },
    });
  }

  prepare(): void {
    this.error = '';
    // Position + job description are MANDATORY — the interview needs role
    // context. Only the resume stays optional.
    if (!this.validateRole()) return;
    this.preparing = true;
    this.roleService.saveLocal(this.targetPosition, this.jobDescription);
    this.resumeService.prepareInterview(this.resumeText.trim(), this.jobDescription.trim()).subscribe({
      next: () => {
        this.preparing = false;
        // Preparing with a role also files it into the library (active), so
        // the table mirrors what interviews actually ran on.
        const go = () => this.router.navigate(['/interview']);
        this.ensureRoleSaved(
          this.targetPosition.trim(),
          this.jobDescription.trim(),
          () => go(),
          () => go(),
        );
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
