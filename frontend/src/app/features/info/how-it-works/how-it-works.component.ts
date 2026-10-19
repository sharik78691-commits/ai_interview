import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

/** /how-it-works — the real flow, including where data goes at each step. */
@Component({
  selector: 'app-how-it-works',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="container info">
      <header class="info-head">
        <h1>How it works</h1>
        <p class="lead">From sign-up to live answers in a few minutes — and what happens to your data at each step.</p>
      </header>

      <section>
        <h2>Getting set up</h2>
        <ol class="steps">
          <li class="card">
            <h3>Create your account</h3>
            <p class="muted">Sign up with email and password or with Google.</p>
          </li>
          <li class="card">
            <h3>Add your resume and target role</h3>
            <p class="muted">Upload a resume (PDF, DOCX or TXT) or paste it, check the extracted text, and save the job title and description you are preparing for. These are stored in your account so you can reuse them.</p>
          </li>
          <li class="card">
            <h3>Choose a plan if you need one</h3>
            <p class="muted">Plans are one-time payments for a fixed number of days, confirmed by the payment gateway before they activate. <a routerLink="/pricing">See pricing</a>.</p>
          </li>
        </ol>
      </section>

      <section>
        <h2>During an interview session</h2>
        <ol class="steps">
          <li class="card">
            <h3>Start a session and share audio</h3>
            <p class="muted">Pick the meeting tab or window whose audio you want transcribed, and optionally turn on your microphone. You can also just type questions.</p>
          </li>
          <li class="card">
            <h3>Questions are detected</h3>
            <p class="muted">Interviewer audio is cut into short clips at natural pauses and sent to a speech-to-text service; your microphone is transcribed by your browser. When a complete question is recognised, it is queued once — repeats aren’t answered twice.</p>
          </li>
          <li class="card">
            <h3>An answer streams in</h3>
            <p class="muted">The question, your resume, the job description and your last few questions go to the AI model you selected. Answer points appear as they are generated, then the full structured answer is shown.</p>
          </li>
          <li class="card">
            <h3>You stay in control</h3>
            <p class="muted">Change answer length or model at any time, and always check the suggestion before using it. Transcripts and answers are not saved to your account.</p>
          </li>
        </ol>
      </section>

      <section class="callout">
        <p>
          Use OyeInterview responsibly: follow the rules of each interview process. Read the
          <a routerLink="/acceptable-use">Acceptable Use Policy</a> and how we handle data on the
          <a routerLink="/trust">Trust page</a>.
        </p>
      </section>

      <div class="cta-row">
        <a routerLink="/register" class="btn-primary">Create an account</a>
        <a routerLink="/features" class="btn-ghost">All features</a>
      </div>
    </div>
  `,
})
export class HowItWorksComponent {}
