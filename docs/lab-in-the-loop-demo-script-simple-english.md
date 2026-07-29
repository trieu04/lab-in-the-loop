# Lab-in-the-Loop System Demo Script — Simple English

## Overview

This document is a simple English script for a live system demo.

The demo shows how the system can:

1. Read an experiment idea from the canvas.
2. Use trusted internal information.
3. Create a structured experiment setup.
4. Create a mock experiment result.
5. Suggest another round or close the experiment.
6. stop safely when information is missing.

> **Important:** This demo uses a mock robot and mock results. It does not run a real laboratory experiment.

## Main Message

The system helps a scientist move from an idea to a clear experiment plan.

The scientist stays in control. The system does not start work from a loose note. The scientist must connect items on the canvas to show the next action.

The system also checks evidence, cost, data rules, and system state before it writes a new result.

## Simple System Flow

```text
Internal documents
        ↓
Knowledge group on the canvas
        ↓
Experiment idea
        ↓
Experiment setup
        ↓
Mock robot
        ↓
Mock result
        ↓
Next experiment or close
```

## Before the Demo

Prepare these items before the presentation:

- Start the Canvus server and open the correct canvas.
- Start `canvus-mcp`.
- Start the artifact service.
- Check that the model API has enough quota.
- Add a `RAGCluster_` knowledge group to the canvas.
- Add one or two internal documents.
- Add an experiment idea note.
- Add one `Robot_` item.
- Check that the canvas has the correct data classification.
- Keep a completed example ready in case the live API is not available.

Example command:

```bash
uv run lab-agent once --canvas <canvas-id>
```

## Demo Script

### Part 1 — Introduction

**Action on screen:** Show the full canvas.

**Say:**

> Hello everyone. Today I will show our Lab-in-the-Loop system.
>
> This system helps scientists plan and review experiments.
>
> We use a visual canvas. The canvas shows the experiment idea, trusted documents, the experiment setup, and the result.
>
> The scientist is always in control. The system only moves forward when the scientist creates the correct connection.

### Part 2 — Explain the Main Items

**Action on screen:** Point to the internal documents, the knowledge group, the idea note, and the robot item.

**Say:**

> These are our trusted internal documents.
>
> This group is called `RAGCluster_`. It tells the system which information it can use.
>
> This note contains the scientist's idea.
>
> This `Robot_` item represents experiment execution.
>
> In this demo, the robot is only a mock robot. It does not control real laboratory equipment.

### Part 3 — Explain the Connector

**Action on screen:** Draw a connector from `RAGCluster_` to the idea note.

**Say:**

> A connector is more than a line.
>
> It is also a command for the system.
>
> This connector means: use this trusted knowledge to create an experiment setup for this idea.
>
> A note without the correct connector will not start the workflow.

### Part 4 — Create the Experiment Setup

**Action on screen:** Run the agent once.

```bash
uv run lab-agent once --canvas <canvas-id>
```

**Say while the system is working:**

> The agent is now reading the connected information.
>
> It collects only the evidence needed for this task.
>
> It then checks three important things.
>
> First, is the evidence strong enough?
>
> Second, are all citations valid?
>
> Third, are there any unclear short names or acronyms?
>
> The system creates a setup only when these checks pass.

### Part 5 — Open the Setup

**Action on screen:** Open `[EXP:Setup v001]`.

**Say:**

> The system has created the first experiment setup.
>
> The setup contains the hypothesis, materials, conditions, steps, expected results, and success rules.
>
> It also contains citations. These citations show which internal information supports the setup.
>
> This is not only text on the canvas.
>
> It is a structured and versioned record. This means the system can track changes over time.

**Optional short explanation:**

> The Browser window is the view that we can see. The structured artifact is the saved record behind that view.

### Part 6 — Explain Safety and Control

**Action on screen:** Keep the Setup open and point to its evidence or status fields.

**Say:**

> The AI model can read approved information, but it cannot directly change the canvas.
>
> The main system controls all canvas changes.
>
> Before a model call, the system also checks the data classification, the approved model, the model price, and the available budget.
>
> If one of these checks fails, the system stops safely.

### Part 7 — Create a Mock Result

**Action on screen:** Connect `[EXP:Setup v001]` to `Robot_`.

**Say:**

> Now I connect the experiment setup to the robot.
>
> This connection means: run this setup.
>
> Again, today we use a mock robot. The result is a simulation for the demo.

**Action on screen:** Run the agent again.

```bash
uv run lab-agent once --canvas <canvas-id>
```

**Say:**

> The system is now creating a mock result.
>
> The result stays connected to the experiment setup and the current experiment round.

### Part 8 — Open the Result

**Action on screen:** Open `[EXP:Result v001]`.

**Say:**

> This is the first experiment result.
>
> It includes a summary, observations, measurements, quality notes, and limits.
>
> The result is clearly marked as mock data.
>
> This is important because users must know the difference between simulated data and real laboratory data.

### Part 9 — Continue or Close the Experiment

**Action on screen:** Connect `[EXP:Result v001]` back to `[EXP:Setup v001]`.

**Say:**

> This new connection asks the system to review the result.
>
> The system compares the result with the setup and the success rules.
>
> It can suggest another experiment round, or it can close the experiment.

**Action on screen:** Run the agent again.

```bash
uv run lab-agent once --canvas <canvas-id>
```

#### If the System Creates Another Round

**Say:**

> The system found that another round may be useful.
>
> It has created a new version with updated conditions.
>
> The old version is still available, so we can see the full experiment history.

#### If the System Closes the Experiment

**Say:**

> The system has closed the experiment loop.
>
> It records why the experiment stopped, what we learned, and what a human should do next.
>
> The loop can also stop because of a round limit, a time limit, a cost limit, or no useful progress.

### Part 10 — Show Restart Safety

**Action on screen:** Run the same command one more time.

```bash
uv run lab-agent once --canvas <canvas-id>
```

**Say:**

> I am running the agent again.
>
> The system remembers completed work.
>
> It does not create the same setup or result twice.
>
> This is important when the service restarts or when a temporary error happens.

**Action on screen:** Run the integrity check.

```bash
uv run lab-agent integrity
```

**Say:**

> This command checks the local database and the audit history.
>
> The audit history helps us understand what the system did and why.
>
> It stores safe technical records such as IDs, decisions, and hashes. It does not store full secret documents or API keys.

### Part 11 — Show the Needs Input Case

**Action on screen:** Show a prepared idea with missing information or an unknown acronym.

**Say:**

> Now I will show a safety case.
>
> This idea does not have enough clear information.
>
> The system should not guess and create an unsafe experiment setup.

**Action on screen:** Run the agent and show `[EXP:Needs Input]`.

**Say:**

> The system created a `Needs Input` item instead of an experiment setup.
>
> It explains what information is missing.
>
> A scientist can review the message, add the missing information, and try again.
>
> This keeps the problem visible and keeps the human in control.

## Final Summary

**Action on screen:** Return to the full canvas.

**Say:**

> Let us review the full process.
>
> We started with trusted internal documents and a scientist's idea.
>
> The scientist used connectors to control each step.
>
> The system created a grounded experiment setup.
>
> It created a clearly marked mock result.
>
> It then suggested another round or closed the experiment.
>
> The system also checked evidence, data rules, model cost, budget, duplicate work, and audit history.
>
> The main value is not only AI-generated text.
>
> The main value is a clear, controlled, and traceable experiment workflow.

## Honest Scope Statement

Use this statement near the end of the demo:

> Today we demonstrated a grounded and controlled mock experiment loop.
>
> The current system can create experiment setups, mock results, next-round suggestions, closed records, and Needs Input messages.
>
> Real robot execution, real wet-lab work, scientist approval buttons, laboratory manager approval, and external analysis systems are future work.

## Short Version for a Five-Minute Demo

If time is short, use this flow:

1. Show the canvas and explain the four main items.
2. Connect `RAGCluster_` to the idea.
3. Run the agent and open the Setup.
4. Connect the Setup to `Robot_`.
5. Run the agent and open the mock Result.
6. Connect the Result back to the Setup.
7. Run the agent and show the next round or Closed artifact.
8. End with the final summary.

### Five-Minute Opening

> Hello everyone. This is our Lab-in-the-Loop system.
>
> It helps a scientist move from an idea to a structured experiment plan and a reviewable result.
>
> The canvas is not only a visual board. The connectors also tell the system what action to take.

### Five-Minute Closing

> This demo showed a full mock experiment loop.
>
> The workflow is grounded in trusted information, controlled by the scientist, protected by system rules, and recorded for later review.
>
> The next goal is to connect this safe workflow to more advanced simulation, human approval, and real laboratory systems.

## Simple Answers for Common Questions

### What is Lab-in-the-Loop?

> It is a system that connects scientists, trusted information, AI models, and experiment tools in one controlled workflow.

### Why do we use connectors?

> Connectors show clear user intent. They tell the system which information to use and which action to take.

### Can the AI change the canvas by itself?

> No. The AI can read approved information and return structured output. The main system controls canvas changes.

### Is the result real laboratory data?

> No. The current demo uses mock results. Real laboratory execution is future work.

### What happens when information is missing?

> The system stops and creates a Needs Input message. It does not guess.

### How does the system prevent duplicate work?

> It saves workflow state in a local database. After a restart, it can see which actions are already complete.

### How does the system control cost?

> It checks the approved model price and available budget before it sends a model request.

### What is an artifact?

> An artifact is a structured system record, such as an experiment setup, result, closed report, or Needs Input message.

### What is grounding?

> Grounding means the system uses trusted evidence and valid citations instead of creating an answer without support.

### What is an audit history?

> It is a safe record of important system actions and decisions. It helps users check what happened later.

## Presenter Tips for Simple English

- Speak slowly.
- Use short sentences.
- Pause after each screen action.
- Explain one new term at a time.
- Say “mock result” every time you discuss simulated output.
- Do not use long technical words when a simple word is enough.
- Do not say that the system controls a real laboratory today.
- Keep a completed canvas ready as a backup.
- If the model API fails, explain the safety behavior and continue with the prepared example.

## Backup Message for a Live API Error

If the model API returns an error, say:

> The external model service is not available now.
>
> Our system recorded the failed attempt and did not write an incomplete artifact to the canvas.
>
> This is an important safety feature.
>
> I will continue with a prepared example so you can see the expected workflow.

## References

- [Lab-in-the-Loop use case specification](lab-in-the-loop-use-case-specification.md)
- [Experiment workflow](experiment-workflow.md)
- [System architecture](system-architecture.md)
- [Setup and operations](setup-and-operations.md)
- [Development roadmap](development-roadmap.md)
