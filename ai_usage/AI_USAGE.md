# How I used AI

Tool: Claude Code (desktop app, model Claude Opus 5.5), used as a coding agent
on my machine in one session across the 72-hour window. Every prompt I typed
is in [prompt_history.md](prompt_history.md).

## Where AI accelerated the work
- Breaking the brief down into plain-language concepts (ledger, lots, ground
  truth, label leakage) with examples, before any code was written.
- Producing the hand-worked 6-event example (customer Asha) that became the
  first test case, so the code had to reproduce numbers worked out in advance.
- Writing the code, tests and documentation milestone by milestone, and running
  the generator, tests and checks on my machine.
- Debugging at scale: profiling the generator, and diagnosing the two problems
  the checks exposed (the SQLite load that hung because foreign-key columns
  were not indexed, and checksums that differed because of Windows line endings).

## Where I relied heavily on AI
- The implementation: practically all of the Python code (engine, generator,
  anomaly injection, detector, 75-check validation suite, export), the SQL
  schema, the 54 tests, the README and the docs were written by the AI.
- The detailed modelling choices inside the approach: FIFO lots and
  `lot_allocations`, the debt path for returns after points were spent,
  idempotent redemption keys, the customer-behaviour distributions, and the
  PASS / EXPECTED / FAIL classification.
- This disclosure itself was drafted by the AI from the session record at my
  request close to the deadline, limited to what the prompt history shows.

## What I personally decided, reviewed or validated
- **The core approach was mine:** process each event (purchase, return,
  redemption) with explicit rules and append every change to a ledger with the
  balance calculated correctly (prompt 6). The AI refined it with lots,
  reversal links and a config-driven rule set.
- **I insisted on understanding before coding:** I asked for the project in
  simple words with examples, a worked 6-event example, and whether to keep the
  data in lists or a database (prompts 5-8), and only then asked to start coding.
- **I reviewed the code in my editor:** I spotted the Pylance "import could not
  be resolved" warnings in VS Code, which led to installing the package in
  editable mode and removing a `sys.path` workaround (prompts 10-11).
- **I directed the work and approved every change:** I chose the order of
  milestones (generator, anomalies, validation, export, docs, fresh-clone
  check), created the public GitHub repository, and approved each commit and
  push.
- **I asked for a full walkthrough** of everything built, so I can explain the
  design, the trade-offs and the likely reviewer questions (prompt 19).

## How the AI's output was verified
I did not take the AI's output on trust; the evidence is in the repository:
- the worked example is a test the engine must match exactly;
- 54 automated tests, including negative tests where deliberately broken data
  must FAIL validation and be rejected by the database constraints;
- full-scale runs (20,000 customers, 100,000 orders): 75 checks with 0 FAIL,
  500 of 500 planted issues found with 0 false positives;
- `reproduce`: 40 output files byte-identical across two full-scale runs;
- a fresh clone in an empty folder, set up only by following the README.

**Limitation, stated honestly:** I did not independently re-derive every
calculation by hand. My verification relies mainly on the worked example, the
automated tests and checks above, and the explanations I asked for during the
session.
