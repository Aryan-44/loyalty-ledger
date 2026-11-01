# Prompt History

**Tool:** Claude Code (desktop application), using Claude Opus 5.5 as a coding assistant.

This document records the prompts I entered during the development of my project. I used AI to help understand the requirements, explore implementation options, develop the solution, troubleshoot issues, and validate the final deliverable. The prompts are presented in chronological order.

## Session 1: Understanding the Requirements and Designing the Rules Engine

**1. Understanding the project**
- I provided the recruitment portal's project link and asked for an explanation of the problem statement, possible approaches, and an overall analysis of the requirements.

**2–4. Reviewing the requirements**
- I signed in, connected the browser, and supplied six screenshots of the Round 2 problem statement, along with the Candidate Project Submission Guidelines and Candidate Project FAQ PDFs.

**5. Clarifying the problem**
- I requested a simple explanation with practical examples because I wanted to understand the problem before beginning implementation.

**6. Proposing the initial approach**
- I proposed using conditional logic to handle purchases, returns, and redemptions. My initial approach was to calculate points according to the event and append each event to a ledger while maintaining the correct balance.

**7. Establishing a reference example**
- I requested a six-event example to understand how the system should behave across multiple transactions.

**8. Choosing the data storage approach**
- I asked whether the data should be stored in Python lists or a database so I could determine an appropriate implementation approach.

**9. Beginning implementation**
- After working through the requirements and the initial design, I confirmed that I understood the project sufficiently to begin coding.

**10–12. Reviewing and improving the test setup**
- I shared a screenshot showing Pylance import-resolution warnings in the test file, approved removing the `sys.path` workaround, and approved the resulting commit.

## Session 1: Building the Synthetic Data Generator

**13–14.**
- I provided my email address as part of the development workflow, instructed the AI to begin implementing the synthetic data generator, and approved the resulting commit.

## Session 1: Implementing Anomalies, Ground Truth, and Detection

**15–16.**
- I directed the next development milestone toward anomaly generation and approved the resulting commit.

## Session 1: Developing the Validation Suite

**17–18.**
- I instructed the AI to proceed with the validation suite and approved the resulting commit.

## Session 1: Reviewing the Implementation and Publishing the Repository

**19. Understanding the implementation**
- I requested a detailed walkthrough of the work completed so far because I wanted to understand the implementation and prepare to answer potential reviewer questions.

**20. Planning the next steps**
- I asked what needed to be done next to progress toward a complete submission.

**21. Publishing the repository**
- I provided my GitHub repository, `https://github.com/Aryan-44/loyalty-ledger`, and asked the AI to connect the project and push the changes.

## Session 1: Export, Manifest, and Reproducibility

**22–24.**
- I approved beginning the export functionality, requested an update on the current development activity, and approved committing and pushing the completed changes.

## Session 1: Data Dictionary and ER Diagram

**25.**
- I instructed the AI to proceed with the project documentation, including the data dictionary and entity-relationship diagram.

## Session 1: Fresh-Clone Verification

**26–27.**
- I requested a fresh-clone check to verify the project setup and approved the resulting commit and push.

## Session 1: Final Review Before Submission

**28. Checking project completeness**
- I asked whether the project was complete or whether any work remained.

**29. Prioritizing the submission**
- With the deadline approaching, I asked what was still outstanding so I could prioritize submitting the repository link.

**30. Preparing the AI usage disclosure**
- I asked the AI to draft the wording for `AI_USAGE.md`, which documents how AI contributed to the project.

---

## Summary of My Role

Throughout the development process, I guided the work from understanding the requirements through implementation, validation, documentation, and repository publication.

My contributions included:

- Analyzing the project requirements and asking for clarification where needed.
- Proposing the initial event-processing and ledger-based approach.
- Exploring data storage options before starting implementation.
- Directing the order of development milestones.
- Reviewing development issues and approving changes.
- Requesting explanations of the implementation to prepare for technical questions.
- Coordinating commits, repository publication, and final verification.
- Reviewing project completeness and prioritizing the submission before the deadline.

Claude Code assisted with implementation, testing, debugging, and documentation. This prompt history records my instructions and decisions; it does not, by itself, establish that I independently wrote or verified every part of the code.
