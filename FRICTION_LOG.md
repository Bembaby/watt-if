# Development friction log

Recorded September 8, 2026 during an AI-assisted build. These observations concern the tools actually used: Strands 1.54.0, Ollama client 0.6.2, local Qwen models, and the browser/Python application. This project uses the allowed Alexa+ simulation route. No Alexa device, Alexa API, MCP integration or Amazon cloud-service onboarding was tested.

## 1. A finish deadline became a start time

- Task: Finish the dryer and dishwasher by 8 pm; have the e-bike ready by 10 pm.
- Steps: Run the first conversational prototype with local Qwen 2.5 7B through Strands, then inspect the actual planning-tool arguments and proposed constraints.
- Expected: Preserve unspecified start times and set only the requested finish deadlines.
- Actual: The model invented an earliest start and interpreted an e-bike finish deadline as a start time. The altered constraints produced a false infeasible result.
- Severity: Critical for a scheduling assistant; silently changing the request invalidates the answer.
- Workaround: Typed changes, complete source-clause validation, and explicit start-versus-finish checks now run before planning. Final verification uses local Qwen 2.5 14B. The exact planner never relaxes an infeasible request.
- Suggestion: Important — provide an SDK example that grounds every tool argument in numbered source clauses and rejects unsupported edits, rather than relying on a prompt alone. The observed interpretation error belongs to the model/application interaction; it is not evidence of a solver or Strands transport defect.

## 2. Tool ordering was treated as a prompt guarantee

- Task: Read the household before proposing changed constraints.
- Steps: Expose a read tool and a proposal tool; ask the model to read first; inspect the actual trace.
- Expected: A read occurs before the proposal consumes household state.
- Actual: The model sometimes selected the proposal tool first.
- Severity: Important; implicit dependencies can make a tool workflow fragile.
- Workaround: The proposal now explicitly performs its required read when necessary. Its trace labels that event `proposal_dependency`, so a dependency read is never presented as a model-selected action.
- Suggestion: Important — document a dependency wrapper pattern for tools, with provenance distinguishing SDK/model calls from application-owned prerequisite calls.

## 3. Quoted evidence was paraphrased

- Task: Validate that each model change is supported by the user's actual instruction.
- Steps: Require quoted evidence, then run real-model tests of shared chore deadlines and contradictory requests.
- Expected: Exact source text suitable for deterministic validation.
- Actual: The model paraphrased or shortened text, causing validation failures and risking loss of shared-job context.
- Severity: Important.
- Workaround: The server numbers the source clauses. The model returns clause IDs; the server resolves original text and checks all named chores and relevant instructions. IDs are not sufficient by themselves: bounds, full-clause intent and coverage are still validated.
- Suggestion: Important — include a compact structured-output provenance example with immutable source IDs and adversarial tests for cropped clauses, negation and omitted instructions.

## 4. Correct tool results did not guarantee correct narrative

- Task: Explain a proposed schedule and its finish times.
- Steps: Inspect free-form model replies alongside validated tool results.
- Expected: The prose preserves the computed schedule exactly.
- Actual: Initial experiments included inaccurate free-form summary text despite useful structured results.
- Severity: Important; a readable answer can still mislead a person.
- Workaround: Successful schedule summaries now use a deterministic template from the validated proposal. The trace records `reply_source=validated_proposal_template`.
- Suggestion: Important — offer a documented hybrid pattern: model-driven interpretation with application-rendered factual summaries and explicit attribution.

## 5. Local model size and output budgets changed practical usability

- Task: Complete a tool-calling turn within bounded latency and output limits.
- Steps: Try smaller local models, inspect stop reasons and token usage, then test Qwen 2.5 14B on the available GPU.
- Expected: A completed validated proposal without an unbounded loop or hidden fallback.
- Actual: Some smaller-model experiments hit the configured output limit. The larger verified model requires about 9 GB of model storage and sufficient local memory. One cold run took about 30 seconds; subsequent verified runs took about 4–7 seconds.
- Severity: Important for setup and user expectations.
- Workaround: Pin the tested model and dependencies, expose real availability status, show progress, bound turns/tokens/time, retain failed diagnostics, and keep manual time controls available. There is no paid or hosted fallback.
- Suggestion: Important — document tested local tool-calling configurations and show model availability separately from successful inference. Nice-to-have — a concise startup preflight explaining storage, memory and cold-load latency.

## Verification after these repairs

The final recorded configuration passed 44 automated regression tests, 3,546 independent scheduling scenarios, 29 approval/calendar/HTTP/concurrency checks, 12 targeted grounding checks, and six actual model scenarios. The continuous demonstration separately performs three real model runs and a human-style approval/download interaction. These are finite examples, not a reliability percentage or a guarantee of general English understanding. See [the evidence directory](evidence/README.md).
