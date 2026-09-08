# Verification evidence

All household inputs are fictional. JSON model traces contain real local Strands/Ollama inference, not service fixtures. Neither Alexa nor physical appliances were connected.

- `browser.json`: three actual model conversations, review/approval/calendar export, and 390px layout verification.
- `model-api.json`: three additional actual model/API scenarios.
- `recording.json`: the separate continuous demonstration run, with original caption timing, actual model/tool results and source hashes. The video adds explanatory captions below the recorded browser; it does not replace or accelerate inference.
- `grounding.json`: 12 deterministic adversarial constraint checks; no model inference.
- `independent.json`: independent exhaustive scheduling oracle plus approval/calendar/HTTP/concurrency checks; no model inference.
- `ui-regressions.json`: explicit unavailable-model status fixture, with real manual planning and approval requests; no inference.
- `fictional-approved.ics`: calendar actually downloaded during the browser test. October 1, 2026 is a fixed demonstration date using floating local times.
- `desktop.png`, `mobile.png`: actual browser captures after successful model inference.

Initial smaller-model experiments failed through invented start times, confused finish deadlines, paraphrased evidence, tool order and uncontrolled summary prose. Those failures prompted the documented repairs and larger model. These finite passing examples do not establish general natural-language reliability. The complete private development record preserves failed runs; no private household or account material is included here.
