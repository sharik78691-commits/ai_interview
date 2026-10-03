import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

/**
 * AudioService — the INTERVIEWEE'S OWN MICROPHONE.
 *
 * This is deliberately kept separate from the interviewer/meeting audio
 * (InterviewerAudioService). Muting here disables only the candidate's mic so
 * it can never be used for interviewer question detection.
 */
@Injectable({ providedIn: 'root' })
export class AudioService {
  micGranted$ = new BehaviorSubject<boolean>(false);
  /** Microphone is live but muted (tracks disabled, not released). */
  micMuted$ = new BehaviorSubject<boolean>(false);
  private stream: MediaStream | null = null;

  async requestMic(deviceId?: string): Promise<MediaStream> {
    const constraints: MediaStreamConstraints = deviceId
      ? { audio: { deviceId: { exact: deviceId } } }
      : { audio: true };
    this.stream = await navigator.mediaDevices.getUserMedia(constraints);
    this.micGranted$.next(true);
    this.micMuted$.next(false);
    return this.stream;
  }

  /** Mute/unmute the candidate's microphone without losing the stream. */
  setMuted(muted: boolean): void {
    const tracks = this.stream?.getAudioTracks() ?? [];
    tracks.forEach((t) => (t.enabled = !muted));
    this.micMuted$.next(muted);
  }

  toggleMute(): boolean {
    const next = !this.micMuted$.value;
    this.setMuted(next);
    return next;
  }

  get muted(): boolean {
    return this.micMuted$.value;
  }

  getStream(): MediaStream | null {
    return this.stream;
  }

  async listMics(): Promise<MediaDeviceInfo[]> {
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      return devices.filter((d) => d.kind === 'audioinput');
    } catch {
      return [];
    }
  }

  stopAll(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.micGranted$.next(false);
    this.micMuted$.next(false);
  }
}
