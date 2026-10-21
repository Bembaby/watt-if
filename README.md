# Watt If

**Make room for your evening.** A working household scheduling application built on PeakShift, with a Strands agent, checked time constraints, a visual evening plan, and a human-approved advisory calendar.

[Watch the September8 baseline demonstration](https://youtu.be/JFKJRdyYYUI) · [Development friction log](FRICTION_LOG.md) · [Historical verification evidence](evidence/README.md)

The October 21 upgrade adds a textured midnight/cobalt interface with licensed marble art, self-hosted typography, visible constraint review, retained unresolved requests, explicit manual replacement and persistent exact-start semantics. The existing submitted version and video are preserved. See [upgrade verification](VERIFICATION_UPGRADE.md) for current evidence and publication gates. The historical demo does not show this redesign.

The [refreshed demonstration candidate](demo/README.md) includes synthetic narration, captions, actual continuous execution and asset credits. It awaits final publication approval. The conversation supports a deliberately small command vocabulary; use the documented examples or the manual controls for unsupported wording.

![October 21 redesigned interface; separate traces document real inference](evidence/upgrade-20261021/interface-desktop.png)

This is a **simulated Alexa+ experience**, using the alternative allowed by the [Amazon Developer Hackathon rules](https://amazonappdev2026.devpost.com/rules). It does not connect to Alexa, start appliances, use live electricity prices, or measure household savings. Every load and forecast is fictional. All calendar events use the clearly labeled demonstration date **October 1, 2026**, as floating local times.

## Run

Python 3.12 is the tested runtime. Install the pinned application dependencies below. The deterministic optimizer does not need a model service; the time controls remain available without inference. The conversation needs a tool-capable Qwen 2.5 14B model through Ollama. Model files require several gigabytes of disk space and sufficient memory.

```sh
python -m venv .venv
# Activate this environment, then:
python -m pip install -r requirements.txt
ollama pull qwen2.5:14b
ollama serve
python app.py
# Open http://127.0.0.1:8793
```

If Ollama is already running, do not start a second server. The app checks the configured model inventory and rejects Ollama cloud model descriptors. It has no automatic hosted fallback or cloud credentials. `/api/status` reports model availability separately from actual inference evidence. “Set times yourself” works without a model.

An operator may instead supply an existing SSH loopback forward to an Ollama instance they control. No rental, SSH connection or billing is initiated by the application. For example, after separately establishing a secure forward on port11435:

```sh
python app.py --ollama-port 11435 --model-location runpod_ssh_tunnel --state remote-demo.sqlite3
```

The UI and traces then identify RunPod execution via SSH, including offline status. The location is explicit operator configuration, not provider attestation. Requests still target only127.0.0.1; no public inference endpoint or arbitrary host URL is accepted. The same qwen2.5:14b model and validators are used. The October21 upgrade was verified through this path with an identical model digest; the temporary GPU was then terminated. Renting a GPU is not required to use the application.

For repeatable runs, provide a fresh state filename:

```sh
python app.py --state demo.sqlite3
python -m unittest discover -s tests -v
python verify_independent.py
```

The full tested Windows/Python 3.12 dependency freeze is in `requirements-lock-windows-py312.txt`; it contains Windows-specific transitive packages. Use `requirements.txt` for other platforms. The independent verifier writes its result under ignored `verification-output/` and performs no model inference.

The server binds only to `127.0.0.1`; keep it local. It validates Host and Origin, rejects cross-site requests and only accepts bounded JSON bodies for mutations. SQLite files contain local conversation-derived constraints and evidence; they are excluded from source publication. To start another fictional household, use another state file. Do not upload private household information to an issue or public demo.

## Try it

1. Preview the default plan and inspect the actual time slots.
2. Ask: “Keep the dryer and dishwasher finished by 8 pm. Have the ebike ready by 10 pm.”
3. Inspect the proposed schedule and the tool trace. A model interpretation must still be reviewed.
4. Approve the advisory calendar, then download its `.ics` file. Approval never operates equipment.
5. Try an impossible request: “Start the dryer at 9 pm. Keep quiet after 8 pm.” No infeasible calendar can be approved.
6. Use the time controls to resolve constraints or leave an optional chore out. Leaving work out changes the scenario; it is not an energy-efficiency gain.

## What the agent does

The Strands SDK runs Qwen through the local Ollama provider. The agent interprets requested changes and calls a planning tool against the current fictional household. The tool reads its required household snapshot as an explicit dependency if the model has not already read it. The trace distinguishes that dependency from a model-selected read.

The model selects typed constraints and numbered source clauses. The server resolves the original clause text and validates every edit before exact planning. The model does not have to reproduce quotations, and it cannot substitute an invented quotation for the original instruction. The trace retains source IDs, resolved text, validated changes and actual tool results.

Deterministic code validates constraints and solves the small scheduling problem. Displayed schedule summaries are rendered from the validated result, not unconstrained model prose; `reply_source` records this. This prevents a correctly computed finish time being described incorrectly by the model. Only successful completed agent interactions can persist a proposal. Failed tool calls retain diagnostic receipts and never trigger a scripted model fallback.

The supported interface is intentionally narrow: explicit evening start times, finish deadlines, a quiet-time cutoff, and skip/restore instructions for the named chores. Use short direct sentences. Conditional or negated requests may require clarification. General conversation understanding is not guaranteed; the time controls provide an inspectable alternative.

An exact start persists separately from a not-before time and a finish deadline. Changing an exact start never creates a hidden finish deadline. Manual controls expose the exact-start choice. Unsupported companion chores are rejected rather than silently dropped. This is a deliberately bounded command vocabulary.

Failed or clarification-only model requests persist across restart. Retry wording retains the original request,and follow-ups retain unresolved obligations. Approval remains blocked until a checked plan resolves the current request or the person explicitly replaces it using reviewed manual constraints. Revision checks and pending-request IDs reject stale replacements. Already-approved historical calendars remain exportable.

Each new proposal creates a revision. Approval requires its exact ID and current revision. An older proposal cannot be newly approved after a newer request, including an infeasible request. An existing historical approval can still export its original calendar, with stored source/approval integrity checks. The agent has no approval or export tool.

```mermaid
flowchart LR
  U[Changed evening plans] --> A[Local Strands and Qwen]
  A --> R[Read household]
  A --> C[Checked constraint changes]
  C --> P[PeakShift exact planner]
  P --> V[Visual proposal and actual trace]
  V --> H[Human reviews current revision]
  H --> I[Advisory calendar download]
```

## Model and boundaries

The fictional household has 12 half-hour slots from 18:00 through midnight, three explicitly optional jobs, a background load, invented tariffs and invented carbon intensities. Noisy jobs must finish by the quiet-time cutoff. Earliest start, latest finish and omission can be changed per job.

The planner minimizes peak watts first, then modeled carbon, then tariff cost. Lower price is not guaranteed when a lower peak has higher cost. Energy is compared only for the same included jobs. This established branch-and-bound algorithm has a finite search budget and reports whether optimality is proved. It does not model appliance safety, circuits, batteries, temperatures, real tariffs or utility programs. Never use it to defer medical devices, refrigeration, critical heating or other essential equipment.

## Provenance and verification

October 21 verification: 58 regressions pass. The independent oracle found no mismatch across 3,546 scenarios and 140,042 assignments; 29 workflow checks pass. Three fresh Strands/Qwen requests through an operator-owned RunPod SSH forward demonstrated shared deadlines, an infeasible exact-start/quiet-hours conflict, and correction requiring new approval. Both actual calendars match their approval-bound exports byte for byte. The continuous browser take is 39.767 seconds; warmed model turns took 12.156, 6.813 and 9.828 seconds. See the [execution evidence](evidence/upgrade-20261021/README.md) and [verification limits](VERIFICATION_UPGRADE.md). Fresh local inference on this upgrade was not completed; historical local results below remain separately dated.

`planner.py` is preserved byte-for-byte from the existing MIT-licensed PeakShift project: SHA-256 `01c594828a0068893a1808ebc21e6202cbcafe23f1b828f17695481a4b35a8ed`. The original creation date is not established by the imported archive, so this project discloses the reuse instead of claiming a wholly new solver.

The conversation layer, household constraints, persistent revisions, approval/calendar workflow, interface and associated tests were developed September 8, 2026. This is substantial new behavior, not a renamed copy of the original JSON optimizer. Created with substantial AI assistance.

The verified runtime is **Strands 1.54.0, Ollama client 0.6.2, and local Qwen 2.5 14B**, tested on Windows/Python 3.12 with an RTX 4080 SUPER. The model download is about 9 GB; local hardware needs sufficient memory. The smaller models tried during development did not meet this workflow's quality or output-budget requirements.

September 8 verification:

| Check | Actual result |
| --- | --- |
| Unit, SDK-fixture and HTTP regression suite | 44 passed; fixtures are not model evidence |
| Independent scheduling oracle | 3,546 scenarios, 140,042 enumerated assignments, no mismatch |
| Independent approval, calendar, HTTP and concurrency checks | 29 passed |
| Targeted negation, cropped-clause and omitted-instruction checks | 12 passed |
| UI regressions, including explicit offline-status fixture | 5 passed |
| Actual model/browser flows | Dinner deadlines, infeasible conflict, skip/recovery, approval and ICS download passed |
| Additional actual model/API flows | Default plan, quiet cutoff and e-bike finish deadline passed |

These six model scenarios are finite examples, not a reliability percentage or a general English-understanding benchmark. The browser also passed at 390px width without page overflow. Inference receipts include the actual model digest, source hashes, tool calls, validated constraints and timings. Cold model startup took about 30 seconds in the recorded browser test; later calls in that test took about 4–7 seconds.

Initial testing found invented start times, an incorrect finish/start interpretation, paraphrased evidence, model tool-order failures and inaccurate free-form summary text. Numbered source clauses, complete-instruction validation, explicit dependency reads, checked summary rendering and the larger local model address the demonstrated failures. Historical evidence is retained. No actual Alexa execution, measured household savings or award is claimed. This local upgrade has not been pushed,published or applied to the previously submitted contest entry.
