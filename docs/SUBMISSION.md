# OpenAI Build Week submission

## Project

Yigdesk

## Category

Work & Productivity

## One-line description

Yigdesk lets an office agent preview a workbook-backed business consequence and inspect its exact evidence before any action is possible.

## Description

A synthetic renewal request arrives by email asking for a 12% discount. Yigdesk reads a generated XLSX without changing its bytes, evaluates a fixed five-formula scenario adapter, and produces a consequence packet showing net ARR, impact, gross margin, policy headroom, and five bound evidence cells. With complete evidence it reports <code>READY FOR CFO</code>; with missing cost evidence it returns an honest <code>HOLD</code> and withholds the memo.

The browser exposes a read-only grid and model inspector so judges can select an object and inspect its formula and lineage. The public repository deliberately has no mutation or commercial trust-service endpoint.

## Potential impact

Teams increasingly ask agents to act across email and business models. Yigdesk addresses the gap between plausible prose and a decision grounded in inspectable evidence. It makes refusal visible when evidence is incomplete.

## Use of Codex and GPT-5.6

Codex was the primary engineering collaborator. It helped select and scope the workflow, implement the Python/JavaScript experience, challenge unproven read-only and refusal claims, correct evidence binding, design the open/private boundary, and execute unit plus real-browser acceptance tests. The submission video should narrate these decisions explicitly.

## Judge instructions

~~~bash
python -m pip install -r requirements.txt
npm ci
python -m yigdesk.app
~~~

Open <http://127.0.0.1:8787>, run both scenario buttons, and optionally execute <code>python -m pytest</code> plus <code>npm run test:e2e</code>.

## Submission checklist

- [ ] Working project and category
- [ ] Project description
- [ ] Public YouTube demo under three minutes
- [ ] Audio explains Codex and GPT-5.6 usage
- [ ] Repository available to judges
- [ ] README includes setup and synthetic sample data
- [ ] Primary <code>/feedback</code> Codex Session ID

Repository: <https://github.com/Henghenggao/yigdesk>

If kept private, share it with <code>testing@devpost.com</code> and <code>build-week-event@openai.com</code> as required by the official challenge page.

Official references: [OpenAI Build Week](https://openai.com/build-week/) and [Devpost requirements](https://openai.devpost.com/).
