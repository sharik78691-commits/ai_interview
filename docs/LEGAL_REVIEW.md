# Legal & Content Review Checklist

The public pages (`/privacy-policy`, `/terms`, `/refund-policy`, `/acceptable-use`,
`/trust`, `/features`, `/how-it-works`, `/contact`, homepage) were rewritten on
2026-10-19 to match what the code actually does. They are **drafts**: nothing here is
legal advice, and the items below need the operator's input (ideally with a lawyer)
before the policies are relied on. Each item is also an HTML comment (`<!-- CONFIRM: … -->`)
next to the affected text. Angular strips these comments from the built site.

## Operator identity & contact

- [ ] Legal name of the operator (individual / sole proprietorship / company) and postal address — Privacy §intro, Terms §1, Contact. Payment gateways also require this for KYC.
- [ ] `support@oyeinterview.com` exists and is monitored — Privacy §11, Terms §15, Refund, AUP, Trust.
- [ ] Optional dedicated security contact — AUP §6.
- [ ] Support hours and response time — Contact (the previous unverified "Mon–Sat 10–7 IST" and "1–2 business days" text was removed).
- [ ] Grievance officer details if required (India: DPDP Act 2023 / IT Rules 2021) — Privacy §7.

## Data practices

- [ ] Hosting providers and regions (website, API, database) and whether data leaves India — Privacy §3.
- [ ] Retention periods: payment records (accounting/tax), activity log (currently kept indefinitely; consider implementing a purge), server logs — Privacy §6.
- [ ] Confirm no analytics or advertising scripts are injected outside the codebase (e.g. hosting dashboard) — Privacy §5.
- [ ] Decide whether to commit to notifying users of material policy changes (not implemented) — Privacy §10.

## Commercial terms

- [ ] GST registration status, whether prices include GST, GSTIN if registered — Terms §4, Pricing FAQ.
- [ ] Liability cap (e.g. fees paid in the last N months) — Terms §12.
- [ ] Governing law and court jurisdiction (city) — Terms §13.
- [ ] Refund review timeline you can commit to — Refund §5.
- [ ] Wording for "service unavailable" refunds — Refund §3.

## Content corrected (was inaccurate against the code)

| Old claim | Reality / new wording |
|-----------|-----------------------|
| Plans "auto-renew" and can be cancelled in settings | No recurring payments exist. All pages now say prepaid, no auto-renewal |
| "Only the detected question text reaches our servers" | Interviewer (tab/meeting) audio clips go to our server and a speech-to-text provider. Browser mic recognition may run on the browser vendor's servers (Chrome) |
| "Resume and JD are stored in browser localStorage" | Resumes (file + text) and roles are stored in the account database. localStorage keeps a working copy |
| AI providers: Groq, Gemini, OpenRouter | Also OpenAI and DeepSeek, depending on the model |
| Pricing features "Priority access", "Session history" | Not implemented. Removed |
| JSON-LD `aggregateRating` 4.8 / 128 | No source for these reviews. Removed (fabricated ratings violate Google's structured-data policy) |
| "100% browser mic privacy" | Removed (see the audio row above) |

## Not claimed anywhere (on purpose)

No certifications (ISO/SOC 2), audits, uptime SLAs, testimonials, user counts, company
registration numbers, or "unhackable" security claims. Add them only if they exist and
can be evidenced.
