# Operator brief

Frank's single status page. He reads this instead of answering per-ticket questions, so it must
stand alone: what shipped, what is in flight, what the factory taught us, what only he can
decide. Load the `artifact-design` skill before writing it.

## Convention (Frank, 2026-08-31 and 2026-09-04)

- One **new dated artifact per edition**; never republish an old URL. File
  `operator-brief-YYYY-MM-DD-HHMM.html`, `<title>Operator Brief · YYYY-MM-DD HH:MMZ</title>`,
  favicon 🏭, `label` "Edition YYYY-MM-DD HH:MMZ", one-sentence `description` naming the
  edition's headline. Find earlier editions with `Artifact list`.
- **Every `#N` is a link** to `https://github.com/omniscient/dark-factory/issues/N` (GitHub
  redirects PR numbers). Post-process the body after `</style>` with a regex; never touch the
  CSS (hex colours contain `#`).
- Whoever closes an item adds its row to the next edition (peer-session protocol).
- Times in UTC with a Z; Frank is UTC-4.

## Structure that has worked

1. **Lede** (3-4 sentences): the edition's story and how many decisions are his.
2. **Counters** row: PRs merged, gates cleared, pauses/hours lost, decisions waiting.
3. **Timeline** table (When · State · What happened), one row per event, prose not fragments;
   each row says what was found, what was done, and what it cost.
4. **What the gates taught us**: one short section per structural finding, with the ticket
   that carries the evidence.
5. **Where the backlog stands**: per lane (wave / reliability fillers / waiting on Frank), with
   what dispatches next and why.
6. **Capacity**: measured, not estimated (window pauses, hours of work per window).
7. **Your calls**: numbered, each with a recommendation and the cost of each option. This is
   the only place a question to Frank belongs.

Publish with the Artifact tool, then tell Frank the URL and the one thing he needs to decide.
Also append the edition's date, artifact id and headline to the memory progress log.
