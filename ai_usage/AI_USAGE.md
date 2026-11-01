# How I Used AI

**Tool:** Claude Code (desktop app, Claude Opus 5.5), used as a coding assistant during a single 72-hour development window. I maintained the project direction, made the key decisions, reviewed the implementation, and validated the results. My prompts are documented in [prompt_history.md](prompt_history.md).

## Where AI Accelerated My Work

- **Understanding the problem:** I broke down the project requirements into understandable concepts, including ledgers, lots, ground truth, and label leakage. I used examples to clarify the requirements before moving into implementation.
- **Designing the initial example:** I worked through a six-event example involving a customer named Asha to establish expected results and create a reference case for validating the implementation.
- **Planning and implementing the project:** I organized the work into milestones, including the transaction engine, data generator, anomaly injection, validation, export, documentation, and final reproducibility checks.
- **Testing and debugging:** I ran the generator, automated tests, and validation checks on my machine. When issues arose, I investigated the results and used AI assistance to identify and resolve problems involving SQLite performance, missing foreign-key indexes, and inconsistent checksums caused by Windows line endings.
- **Improving maintainability:** I reviewed the project structure and development workflow, including resolving Python import warnings in VS Code and ensuring the project could be installed and run using the documented setup instructions.

## My Contributions and Responsibilities

- **Defining the core approach:** I established the fundamental requirement of processing purchases, returns, and redemptions as explicit events, maintaining a transaction ledger, and calculating balances according to defined rules. I then refined the approach with AI assistance to handle more complex scenarios.
- **Making design decisions:** I directed the discussions around database storage, event processing, lot tracking, return reversals, redemption handling, and validation. I evaluated the proposed approaches and used them to shape the final implementation.
- **Understanding the implementation:** Before proceeding with development, I asked for plain-language explanations, practical examples, and a worked six-event scenario. This helped me understand the expected behavior and establish a basis for reviewing the results.
- **Reviewing the code:** I inspected the implementation in VS Code, identified Pylance import warnings, and addressed the underlying environment and installation issues with AI assistance.
- **Managing the development process:** I determined the order of implementation milestones, reviewed the results at each stage, and directed the project toward completion. I also created the public GitHub repository and approved the commits and pushes.
- **Taking responsibility for the final deliverable:** I requested a complete walkthrough of the implementation, design choices, trade-offs, and potential reviewer questions so I could explain the project and prepare to discuss its technical details.

## How I Validated the Results

I used a combination of worked examples, automated tests, validation checks, and reproducibility tests to evaluate the implementation.

- **Reference example:** The engine was tested against a six-event example with predefined expected results.
- **Automated testing:** The project includes 54 tests covering core functionality and negative scenarios involving invalid data.
- **Data validation:** A full-scale run involving 20,000 customers and 100,000 orders completed 75 validation checks with zero failures. The detector identified all 500 deliberately introduced issues, with zero false positives.
- **Reproducibility:** Two full-scale runs produced 40 byte-identical output files.
- **Setup verification:** I tested the project from a fresh clone in an empty directory by following the README instructions.

These checks gave me evidence that the implementation behaved as expected under the tested scenarios. They also helped me identify and resolve issues before completing the project.

## My Approach to AI-Assisted Development

I treated AI as a development assistant rather than as a substitute for understanding the problem. My role involved defining the core approach, clarifying requirements, directing implementation milestones, reviewing outputs, investigating issues, and checking the final results.

AI contributed substantially to generating the implementation, tests, SQL schema, and documentation. I used explanations, examples, and test results to understand the system and assess its behavior.

**I recognize that passing automated tests does not guarantee that every design choice or calculation is correct.** I did not independently re-derive every calculation by hand, so my confidence in the final implementation is based primarily on the reference example, automated tests, validation results, reproducibility checks, and the understanding I developed during the project.
