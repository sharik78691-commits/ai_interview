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
        "A **Saga** is how you keep data consistent across microservices without a distributed transaction.",
        'Each service commits its own local transaction and publishes an event that triggers the next step.',
        'If a step fails, we run **compensating actions** to undo the earlier steps — refund the payment, release the stock.',
        "I'd pick orchestration when the flow has many steps, because one coordinator makes it easier to trace and debug.",
      ],
      simpleExplanation:
        "Think of booking a trip: flight, hotel, car. If the car fails, you cancel the hotel and flight — you don't lock all three systems at once.",
      keyDetails: [
        '**Choreography** — services react to events, no central controller',
        '**Orchestration** — one coordinator drives each step',
        '**Compensation** — undo logic replaces rollback; must be idempotent',
        '**Trade-off** — eventual consistency, more moving parts than 2PC',
      ],
      example:
        'On our order platform, order → payment → inventory ran as an orchestrated saga. When inventory failed, the orchestrator refunded payment automatically, and stuck orders dropped to near zero.',
      followUps: [
        { question: 'What if a compensating action fails?', answer: 'Retry with backoff, keep it idempotent, and park it in a dead-letter queue for manual review.' },
        { question: 'Choreography or orchestration — when would you pick each?', answer: 'Choreography for 2–3 simple steps; orchestration once the flow branches or needs visibility.' },
      ],
      followUpQuestions: [],
    },
    {
      question: 'How does a hash map differ from a balanced tree?',
      questionType: 'technical',
      answerPoints: [
        'The key difference is **ordering versus speed**.',
        'A hash map gives average O(1) lookups but keeps no order; a balanced tree is O(log n) but keeps keys sorted, so it supports range queries.',
        'So I use a hash map for caches and dedup, and a tree for leaderboards or anything that needs "between X and Y".',
      ],
      simpleExplanation:
        'A hash map is a coat check — your ticket takes you straight to the coat. A tree is a sorted shelf — slower to find one item, but easy to scan in order.',
      keyDetails: [
        '**Lookup** — hash map: O(1) average vs tree: O(log n) guaranteed',
        '**Order** — hash map: none vs tree: sorted, range scans',
        '**Worst case** — hash map degrades with collisions; tree stays O(log n)',
        '**When to pick** — hash map for point lookups, tree for ordered queries',
      ],
      followUps: [
        { question: 'What happens during hash collisions?', answer: 'Entries share a bucket; Java switches a crowded bucket to a tree after 8 entries.' },
        { question: 'How would you build an LRU cache?', answer: 'Hash map for O(1) lookup plus a doubly linked list for recency order.' },
      ],
      followUpQuestions: [],
    },
    {
      question: 'Tell me about a time you resolved a production incident.',
      questionType: 'behavioral',
      answerPoints: [
        'During a peak sale, checkout latency spiked and I was the incident commander.',
        'I narrowed it to a deploy from that morning, rolled it back, and added a missing database index.',
        'p99 latency recovered in **25 minutes**, and afterwards I added canary deploys and an SLO alert so we catch it before customers do.',
      ],
      star: {
        situation: 'Checkout latency spiked during peak traffic.',
        task: 'I was incident commander, owning recovery and comms.',
        action: 'Rolled back the deploy, added a DB index, kept stakeholders updated every 15 minutes.',
        result: 'p99 recovered in 25 min; canary + SLO alert added.',
      },
      followUps: [
        { question: 'What was the hardest part?', answer: 'Holding off a risky hotfix — rollback first, root cause second.' },
        { question: 'What would you do differently?', answer: 'Ship the canary earlier; it would have caught this before peak.' },
      ],
      followUpQuestions: [],
    },
    {
      question: 'How would you solve two-sum with optimal complexity?',
      questionType: 'coding',
      answerPoints: [
        "I'd start by mentioning the brute force — check every pair, O(n²).",
        'Then I store each number I have seen in a hash map, so for each x I just check whether target − x is already there.',
        "That's one pass, O(n) time, trading O(n) memory for speed.",
      ],
      approach:
        'Brute force checks all pairs in O(n²). The insight: for each x we only need to know whether target − x appeared earlier — a hash map answers that in O(1).',
      codeHint:
        'def two_sum(nums, target):\n' +
        '    """Return indices of the two numbers that add up to target."""\n' +
        '    seen = {}  # value -> index\n' +
        '    for i, x in enumerate(nums):\n' +
        '        if target - x in seen:  # complement found\n' +
        '            return [seen[target - x], i]\n' +
        '        seen[x] = i\n' +
        '    return []  # no valid pair',
      humanCode:
        'def two_sum(nums, target):\n' +
        '    # number -> where I saw it\n' +
        '    seen = {}\n' +
        '\n' +
        '    for i in range(len(nums)):\n' +
        '        needed = target - nums[i]\n' +
        '        if needed in seen:\n' +
        '            # found the other half, done\n' +
        '            return [seen[needed], i]\n' +
        '        seen[nums[i]] = i\n' +
        '\n' +
        '    return []  # nothing adds up',
      complexity: 'Time: O(n) — single pass. Space: O(n) — the hash map.',
      edgeCases: ['No valid pair', 'Duplicates like [3, 3]', 'Negative numbers'],
      followUps: [
        { question: 'What if the array is sorted?', answer: 'Two pointers from both ends — O(n) time, O(1) space.' },
        { question: 'What about returning all pairs?', answer: 'Keep scanning instead of returning, and dedupe with a set.' },
      ],
      followUpQuestions: [],
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
      followUps: [
        { question: 'Can you go deeper on the trade-off?', answer: 'Name the cost you accepted and why it was worth it.' },
        { question: 'What would you do with more time?', answer: 'Pick one concrete improvement and the result you expect.' },
      ],
      followUpQuestions: [],
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
