import { Injectable } from '@angular/core';
import { AIInterviewResponse } from '../models/interview.models';

@Injectable({ providedIn: 'root' })
export class DemoService {
  DEMO_TRANSCRIPTS: string[] = [
    'Hello, thanks for joining today.',
    'Can you explain the Saga pattern in microservices?',
    'How does a hash map differ from a balanced tree?',
    'Tell me about a time you resolved a production incident.',
    'How would you solve two-sum with optimal complexity?',
  ];

  DEMO_GUIDANCE: AIInterviewResponse[] = [
    {
      question: 'Can you explain the Saga pattern in microservices?',
      questionType: 'technical',
      answerPoints: [
        'Define Saga: sequence of local transactions where each step publishes an event triggering the next.',
        'Contrast choreography (event-driven, no central controller) vs orchestration (central coordinator).',
        'Explain compensating transactions for rollback since 2PC is impractical across services.',
        'Give example: order → payment → inventory, with compensation if payment fails.',
      ],
      star: undefined,
      codeHint: undefined,
      followUpQuestions: ['How do you handle a failing compensating action?', 'Choreography vs orchestration — when would you pick each?'],
    },
    {
      question: 'How does a hash map differ from a balanced tree?',
      questionType: 'technical',
      answerPoints: [
        'HashMap: average O(1) get/put via hashing; unordered; needs good hash + load-factor resizing.',
        'Balanced tree (e.g. red-black): O(log n) ops but keeps keys sorted, supports range queries.',
        'Memory: hash tables use more space; trees have pointer overhead per node.',
        'Mention use cases: caches/dedup → hash map; leaderboards/range scans → tree.',
      ],
      followUpQuestions: ['What happens during hash collisions?', 'How would you implement an LRU cache?'],
    },
    {
      question: 'Tell me about a time you resolved a production incident.',
      questionType: 'behavioral',
      answerPoints: [
        'Set the scene: service, impact, and your on-call role.',
        'Describe triage: dashboards, logs, narrowing the blast radius.',
        'Highlight communication: status updates, stakeholder alignment.',
        'Close with prevention: postmortem, runbook, alert fix.',
      ],
      star: 'S: Checkout latency spiked during peak. T: I was incident commander. A: Rolled back the deploy, added DB index, paged correctly. R: p99 recovered in 25 min; added canary + SLO alert follow-up.',
      followUpQuestions: ['What was the hardest disagreement during the incident?', 'What would you do differently next time?'],
    },
    {
      question: 'How would you solve two-sum with optimal complexity?',
      questionType: 'coding',
      answerPoints: [
        'Brute force O(n²) first, then optimize with a hash map to O(n) time / O(n) space.',
        'Single pass: for each x, check if (target − x) is in the map before inserting x.',
        'Handle duplicates and return indices, not values.',
        'State complexity tradeoff and edge cases: no solution, negative numbers.',
      ],
      codeHint: 'seen = {}\nfor i, x in enumerate(nums):\n    if target - x in seen:\n        return [seen[target - x], i]\n    seen[x] = i',
      followUpQuestions: ['What if the array is sorted?', 'How would you handle all-pairs variants?'],
    },
  ];

  guidanceFor(question: string): AIInterviewResponse {
    const q = question.toLowerCase();
    if (q.includes('saga')) return this.DEMO_GUIDANCE[0];
    if (q.includes('hash')) return this.DEMO_GUIDANCE[1];
    if (q.includes('production') || q.includes('time you')) return this.DEMO_GUIDANCE[2];
    if (q.includes('two-sum') || q.includes('two sum')) return this.DEMO_GUIDANCE[3];
    return {
      question,
      questionType: 'general',
      answerPoints: [
        'Lead with a one-sentence direct answer.',
        'Support with one concrete example from your experience.',
        'Quantify impact where possible (latency, scale, revenue).',
        'Close by tying it back to this role.',
      ],
      followUpQuestions: ['Can you go deeper on the tradeoff?', 'What would you do with more time?'],
    };
  }

  runDemoSequence(callbacks: { onTranscript: (t: string) => void; onGuidance: (g: AIInterviewResponse) => void; onDone?: () => void }): void {
    localStorage.setItem('aia_demo', '1');
    const pairs: Array<[string, AIInterviewResponse]> = [
      [this.DEMO_TRANSCRIPTS[1], this.DEMO_GUIDANCE[0]],
      [this.DEMO_TRANSCRIPTS[3], this.DEMO_GUIDANCE[2]],
      [this.DEMO_TRANSCRIPTS[4], this.DEMO_GUIDANCE[3]],
    ];
    pairs.forEach(([t, g], i) => {
      setTimeout(() => callbacks.onTranscript(t), 700 + i * 3200);
      setTimeout(() => callbacks.onGuidance(g), 1900 + i * 3200);
    });
    setTimeout(() => callbacks.onDone?.(), 700 + pairs.length * 3200);
  }
}
