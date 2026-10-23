import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

interface Feature {
  icon: string;
  title: string;
  text: string;
}

/**
 * /features — every item here is implemented in the current codebase.
 * Keep it that way: do not list roadmap items as features.
 */
@Component({
  selector: 'app-features-page',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="container info">
      <header class="info-head">
        <h1>Features</h1>
        <p class="lead">
          What OyeInterview does today to help you practise and prepare — in the browser, with your own resume and target role.
        </p>
      </header>

      @for (group of groups; track group.title) {
        <section>
          <h2>{{ group.title }}</h2>
          <div class="info-grid">
            @for (f of group.items; track f.title) {
              <div class="card">
                <h3>{{ f.icon }} {{ f.title }}</h3>
                <p class="muted">{{ f.text }}</p>
              </div>
            }
          </div>
        </section>
      }

      <section class="callout">
        <p>
          <strong>Good to know:</strong> AI answers can be wrong — check them before you rely on
          them. Live microphone transcription needs a browser with speech recognition (Chrome or
          Edge); everything else, including typed questions, works in other modern browsers.
        </p>
      </section>

      <div class="cta-row">
        <a routerLink="/how-it-works" class="btn-ghost">How it works</a>
        <a routerLink="/pricing" class="btn-primary">See pricing</a>
      </div>
    </div>
  `,
})
export class FeaturesPageComponent {
  readonly groups: { title: string; items: Feature[] }[] = [
    {
      title: 'Practice-session help',
      items: [
        { icon: '🎧', title: 'Interviewer audio detection', text: 'Share the audio of a mock interview or practice call and OyeInterview transcribes short clips to spot the questions automatically.' },
        { icon: '🎙', title: 'Microphone transcription', text: 'Your own voice is transcribed by your browser’s speech recognition, separately from the interviewer audio.' },
        { icon: '⌨️', title: 'Typed questions', text: 'Type or paste any question when audio isn’t available — you get the same structured answer.' },
        { icon: '⚡', title: 'Streaming answers', text: 'Answer points appear while the model is still writing, then the full structured answer replaces them.' },
        { icon: '🔁', title: 'Follow-up awareness', text: 'Short follow-ups like “why?” are answered in the context of your previous few questions.' },
        { icon: '📏', title: 'Answer length control', text: 'Switch between short, medium and long answers at any time during a session.' },
      ],
    },
    {
      title: 'Answers built around you',
      items: [
        { icon: '📄', title: 'Resume library', text: 'Upload PDF, DOCX or TXT resumes (up to 10 MB), fix the extracted text, keep up to four and pick the active one.' },
        { icon: '🎯', title: 'Target roles', text: 'Save up to four job titles with their job descriptions and switch the active role before an interview.' },
        { icon: '🧩', title: 'Structured output', text: 'Answer points, STAR outlines for behavioral questions, code hints and complexity notes for coding questions, examples, key takeaways and likely follow-ups.' },
        { icon: '🧠', title: 'Latest AI models', text: 'Answers use the AI model we have chosen as the best fit for speed and quality (for example Gemini, Groq, Qwen, OpenAI or DeepSeek models), updated as better models arrive.' },
      ],
    },
    {
      title: 'Account & billing',
      items: [
        { icon: '🔐', title: 'Secure sign-in', text: 'Email and password or Google sign-in, with HttpOnly session cookies and password reset by email.' },
        { icon: '💳', title: 'Prepaid plans', text: 'One-time payments for a fixed number of days through Cashfree — no auto-renewal and no stored cards.' },
        { icon: '🧾', title: 'Billing history', text: 'See your current plan, expiry date and every payment on your Billing page.' },
        { icon: '🌓', title: 'Comfortable to read', text: 'Light and dark themes and adjustable answer text size.' },
      ],
    },
  ];
}
