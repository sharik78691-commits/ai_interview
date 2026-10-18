import { Component, input, signal } from '@angular/core';
import { AnalysisResult } from '../../core/screenshot/screenshot.models';

export interface TextSegment {
  kind: 'text' | 'code';
  value: string;
}

/** Splits ``` fenced blocks out of model text so code renders in <pre>. */
export function splitFences(text: string): TextSegment[] {
  const out: TextSegment[] = [];
  const re = /```[a-zA-Z0-9_+-]*\n?([\s\S]*?)```/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ kind: 'text', value: text.slice(last, m.index).trim() });
    out.push({ kind: 'code', value: m[1].replace(/\n$/, '') });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: 'text', value: text.slice(last).trim() });
  return out.filter((s) => s.value);
}

/** Renders one analysis result as sections, each with a copy button. */
@Component({
  selector: 'app-analysis-result',
  standalone: true,
  templateUrl: './analysis-result.component.html',
  styleUrl: './analysis-result.component.css',
})
export class AnalysisResultComponent {
  readonly result = input.required<AnalysisResult>();
  readonly copied = signal('');
  readonly splitFences = splitFences;

  async copy(id: string, text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    this.copied.set(id);
    setTimeout(() => this.copied() === id && this.copied.set(''), 1500);
  }

  keyPointsText(r: AnalysisResult): string {
    return r.keyPoints.map((p) => `• ${p}`).join('\n');
  }

  followUpsText(r: AnalysisResult): string {
    return r.followUps.map((f) => `Q: ${f.question}\nA: ${f.suggestedResponse}`).join('\n\n');
  }

  allText(r: AnalysisResult): string {
    const parts = [
      `Topic: ${r.topic}`,
      `Expected level: ${r.seniority}`,
      `Questions:\n${r.questions.map((q) => `- ${q}`).join('\n')}`,
      `Direct answer:\n${r.directAnswer}`,
      `Interview answer:\n${r.interviewAnswer}`,
      `Key points:\n${this.keyPointsText(r)}`,
      `Follow-ups:\n${this.followUpsText(r)}`,
    ];
    if (r.code) {
      parts.push(
        `Code explanation:\n${r.code.explanation}`,
        `Complexity:\n${r.code.complexity}`,
        `Edge cases:\n${r.code.edgeCases.map((e) => `- ${e}`).join('\n')}`,
        `Improved answer:\n${r.code.improvedAnswer}`,
      );
    }
    return parts.join('\n\n');
  }
}
