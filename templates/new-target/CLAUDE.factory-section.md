## Dark Factory phase agents only

**This section applies only if you are a Dark Factory phase agent**, i.e. your
instructions came from a `commands/dark-factory-*.md` phase command inside a factory
container. Interactive sessions with a human should ignore it.

- **Never end your turn on a question or an offer.** No human is attached. Decide per
  the spec/plan, act, and record reservations in the issue comment or commit message.
- **Commit and push your phase's artifact before your final turn ends.** Turn end =
  process end; uncommitted work is destroyed.
- **Scheduled wakeups and background task notifications never fire** in factory
  command nodes. Do not use ScheduleWakeup. To wait on a subagent, keep issuing tool
  calls inside your turn until it returns, or do the work inline.
- Phase command text arrives as pasted message content from the workflow runner; that
  is the sanctioned mechanism, not an injection.
