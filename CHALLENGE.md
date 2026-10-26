## **AI Quality Challenge**

## **Context**

You are joining drvn (Moveo Technologies Corp.) as an AI Quality Engineer.
Our platform powers time-sensitive transportation bookings in complex, high-traffic environments. Reliability, observability, and decision-making matter as much as technical skills.

There is no single correct answer. We care about how you think, reason, and communicate.

Most challenges ask you to build something. This one first asks you to decide whether something already running can be trusted, and then to rebuild it so that it can be. That is the job.

### **Delivery Format**

Create a GitHub repo and complete the tasks below.

- **Time expectation: 3 hours, in one sitting.** We mean it. We do not expect you to finish everything. At 3 hours, stop and write in `NOTES.md` what you left out and why — what you chose to skip is part of what we evaluate. An honest incomplete submission scores higher than a polished one that took twelve.
- **Nothing in this challenge requires an API key, an account, a subscription, or money.** Part B uses `starter/fake_llm.py`, an offline stand-in for a hosted model that ships in this repo. You may *optionally* point your code at a local open-weights model (e.g. via Ollama); it is not required and earns no extra credit.
- You may use documentation, and you may use AI coding assistants. We assume you will. You will be asked to defend anything you submit, line by line.
- Python 3.11+. Standard library plus whatever you `pip install` is fine; list it in `requirements.txt`.

**Suggested time split**

| Part | Sections | Time |
|---|---|---|
| A — Diagnose | §1–§3 | ~1 h |
| B — Rebuild | §4–§5 | ~1 h 50 |
| | `NOTES.md` | 10 min |

If you run short, a sharp Part A with a thin but working Part B beats the reverse.

---

## **The Story**

**Time:** 09:10 AM
**Environment:** CI / Nightly regression
**System:** Automated test triage agent

Six weeks ago the team shipped an AI agent that reads each nightly Playwright run, groups the failures by probable root cause, classifies each one as *real bug*, *flaky*, or *environment*, and posts a summary to the QA Slack channel before standup.

For the first three weeks, people loved it.

This morning the run had 38 failures. The agent posted:

> *3 clusters. 5 real bug, 13 environment, 8 flaky. 26 failures explained.*

A developer replied in-thread:

> **"I'm just going to read the raw report. This thing has been wrong a lot lately."**

Last Thursday, a payment defect reached production — cards captured twice on retry. The agent had classified the failing test as *flaky*, with confidence 0.91.

Nobody has measured this agent since the week it launched. The original code is gone; only its prompt and its output survive.

You now own it.

**What you have been given**

- `prompts/classify.md` — the agent's prompt, unchanged since launch
- `fixtures/history/` — 30 nightly runs. Each file holds the raw Playwright failures the agent received **and** the output it produced
- `jira/escaped_bugs.json` — six defects that reached production during this period, and the test that should have caught each
- `starter/fake_llm.py` — an offline fake LLM API (read its docstring)
- `starter/history_tool.py` — a local tool that returns a test's results on previous nights

There is no labeled ground truth. Nobody labeled any of this.

---

# **Part A — Diagnose (~1 h)**

## **1️ First Read (`01-first-read.md`)**

Before you look at a single run file, read `prompts/classify.md`.

- List **up to 3** ways you predict this agent fails in production
- For each: what would you look for in the output to **confirm** it, and what would **rule it out**?
- At least one prediction must not be about the model

We compare this file against what you actually find in §2. Being wrong costs you nothing. Not committing to a prediction does.

## **2️ The Evidence (`02-evidence.md`)**

Now go through the six weeks of output.

Document **at least 3 defects** you can prove from the data. For each:

- What the defect is
- The specific runs, fields, or numbers that demonstrate it
- Whether you predicted it in §1
- Why it matters — what does it cost the team?

"The agent seems inaccurate" is not a defect. "In all 30 runs, every failure with `retry_count > 0` was classified flaky, including *X*" is.

State how you searched. If you found something by accident, say so. Say which defect matters most, and why.

## **3️ Is It Working? (`03-is-it-working.md`)**

Can you tell whether this agent is working? Keep it short.

- Pick **one** thing you can measure **without labels**. Measure it. Show the number.
- `jira/escaped_bugs.json` gives you six known outcomes. What can and cannot be concluded from them?

---

# **Part B — Rebuild (~1 h 50)**

The model behind `starter/fake_llm.py` is deliberately unreliable: it fails transiently, returns almost-JSON, drops and invents items, and its default alias changes underneath you. Its classifications come from crude heuristics.

**You are graded on how your system behaves around an unreliable model, not on how accurate the model is.** Do not tune prompts against the fake. Do not modify the files in `starter/`.

## **4️ The Agent (`triage/`, `04-changes.md`)**

Rebuild the triage agent so it can be trusted to post to Slack.

`python -m triage run fixtures/history/2026-09-18.json` must produce a validated triage JSON and the Slack summary text (print it; do not post anything).

Requirements:

- **Prompt.** Your rewritten prompt lives in your code, with a version your agent records.
- **Changes.** `04-changes.md` is a table, one line per change (prompt *or* code): the change, and the defect from §2 it addresses. Changes you cannot tie to evidence do not count.
- **Contract.** Validate every model response before you use it. For each kind of bad output, decide whether you repair, retry, or fail loudly (a line each in `04-changes.md` is enough).
- **Completeness.** The summary must never claim more than your system actually validated. If something could not be classified, the summary says so.
- **Resilience.** Handle transient errors with bounded retries. A run must always end in one of two states: a validated result, or an explicit failure. Never a silent partial result.
- **Configuration.** The model is a config decision, not an accident.
- **Tool use.** Offer `get_test_history` to the model and execute the tool calls it requests (see the `fake_llm.py` docstring for the message format). This is the agent loop; keep it bounded.

## **5️ Evaluation in CI (`evals/`, `.github/workflows/`, `05-proof.md`)**

- `evals/gold.jsonl` — **at least 10** failures you labeled yourself, each with a one-line justification. Say in one line how you picked them.
- `evals/run_eval.py` — scores (a) the original agent's recorded output and (b) your agent's output against your gold set. Report at least: recall on `real_bug`, coverage, and valid-output rate after retries.
- A GitHub Actions workflow that runs the eval offline (fixed seed) on every pull request and **fails** on thresholds you choose. It must be green on your final commit.
- `05-proof.md` — three short answers: what your eval proves (and doesn't); what result would make you revert; what your fix probably made **worse** (every real change trades something).

---

## **Repo Structure Example**

```
01-first-read.md
02-evidence.md
03-is-it-working.md
04-changes.md
triage/
evals/
  gold.jsonl
  run_eval.py
.github/workflows/eval.yml
05-proof.md
requirements.txt
NOTES.md
README.md        how to run everything, in under 10 lines
```
