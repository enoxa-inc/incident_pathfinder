# Friction Log — Incident Pathfinder

Friction we hit while building Incident Pathfinder on 2026-10-01: an Amazon Bedrock agent on AWS Lambda that
calls an existing MCP server and is presented as a simulated Alexa+ experience. Error messages are quoted as
we saw them.

Severity: **High** = blocked the planned approach · **Medium** = cost significant time or forced a design
change · **Low** = minor annoyance.

---

## 1. Anthropic models blocked in a new AWS account

- **Task attempted:** Use Claude on Amazon Bedrock (Converse API) as the agent model.
- **Steps taken:** `aws bedrock list-inference-profiles` in us-east-1 listed many Claude profiles. Called
  `aws bedrock-runtime converse --model-id us.anthropic.claude-haiku-4-5-20251001-v1:0`.
- **Expected:** A response, as for any model that appears in the profile list.
- **Actual:** `ResourceNotFoundException: Model use case details have not been submitted for this account.
  Fill out the Anthropic use case details form before using the model. If you have already filled out the
  form, try again in 15 minutes.`
- **Severity:** High. Our first-choice model was unavailable on build day.
- **Workaround:** Switched to Amazon Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`), which worked immediately.
  The model ID is a single Terraform variable, so we can switch back later.
- **Suggestion:** Show "requires use-case form" or "invocable: yes/no" in `list-inference-profiles` and the
  console model list, and link the form from the error. A pre-approved path for hackathon accounts would
  remove the blocker.

## 2. Listed inference profile returns AccessDenied

- **Task attempted:** Try another Claude model after #1.
- **Steps taken:** `converse --model-id us.anthropic.claude-sonnet-5` (the profile was listed in the same
  account and region).
- **Expected:** Either a response or the same use-case-form message as #1.
- **Actual:** `AccessDeniedException: anthropic.claude-sonnet-5 is not available for this account. You can
  explore other available models on Amazon Bedrock.`
- **Severity:** Medium.
- **Workaround:** Stopped trying Claude models and used Nova.
- **Suggestion:** Do not list profiles the account cannot invoke, or mark them as unavailable, so developers
  don't have to discover this by trial and error.

## 3. End-of-life model still listed

- **Task attempted:** Compare Nova models for answer quality.
- **Steps taken:** `converse --model-id us.amazon.nova-premier-v1:0` (listed by `list-inference-profiles`).
- **Expected:** A response.
- **Actual:** `ResourceNotFoundException: This model version has reached the end of its life.`
- **Severity:** Low.
- **Workaround:** Used Nova 2 Lite.
- **Suggestion:** Hide end-of-life profiles from the list, or include a lifecycle status field.

## 4. Small model skips tools and repeats earlier answers in multi-turn tool use

- **Task attempted:** A three-turn investigation ("What happened?" → "What changed?" → "What should I do?")
  with Converse tool use and conversation history.
- **Steps taken:** System prompt describing when to use each tool; `toolChoice` auto; previous turns passed
  as messages.
- **Expected:** The model calls the tools each question needs and answers only the new question.
- **Actual:** Nova 2 Lite often answered turn 2 or 3 without calling any tool. In one run it returned its
  turn-1 answer word for word for turn 2.
- **Severity:** Medium. The demo conversation did not work until we changed the design.
- **Workaround:** A per-question plan in the system prompt (required tools plus an "answer focus"),
  `toolChoice: {"any": {}}` on the first round when tools are required, and one nudge message if a required
  tool is still missing.
- **Suggestion:** Document multi-turn tool-use patterns for smaller models (forcing the first tool call,
  avoiding answer repetition), and clarify how well each model supports `toolChoice` "any" / "tool".

## 5. API Gateway's 30-second limit vs. agent loops

- **Task attempted:** Run the full agent loop (several Bedrock rounds plus remote MCP calls) behind an
  API Gateway HTTP API.
- **Steps taken:** Lambda proxy integration, `timeout_milliseconds = 30000`.
- **Expected:** Enough headroom for a multi-step agent.
- **Actual:** The integration timeout cannot exceed 30 s, so a slow model round or slow tool call would fail
  the request.
- **Severity:** Medium (a design constraint rather than a failure; our turns finish in about 5–8 s).
- **Workaround:** Capped the agent at 6 rounds, set a 25 s Bedrock read timeout and a 15 s MCP timeout, and
  chose a fast model.
- **Suggestion:** A documented pattern (or a larger limit for streaming responses) for agent workloads
  behind HTTP APIs.

## 6. No Alexa+ developer access for participants

- **Task attempted:** Build the Alexa+ experience.
- **Steps taken:** Read the track requirements and FAQ.
- **Expected:** A way to run and test an Alexa+ integration.
- **Actual:** Alexa+ developer tools are not available to hackathon participants. The track accepts a
  simulated experience instead.
- **Severity:** Medium. We could not validate the real voice flow.
- **Workaround:** Built a web UI that simulates the Alexa+ conversation (short spoken-style answers,
  follow-up context) and kept the agent backend behind an API that a real integration could call.
- **Suggestion:** Provide a sandbox or simulator (even text-only) so participants can test real Alexa+
  conversation handling.

## 7. MCP Streamable HTTP responses vary between JSON and SSE

- **Task attempted:** Write a minimal MCP client in Python (no SDK) to call an existing MCP server from
  Lambda.
- **Steps taken:** POST JSON-RPC with `Accept: application/json, text/event-stream`.
- **Expected:** One response format.
- **Actual:** `initialize` returned `application/json`, while `tools/list` and `tools/call` returned
  `text/event-stream` (`event:message` / `data:{...}`). Both are valid per the spec, but a naive client
  breaks.
- **Severity:** Low.
- **Workaround:** Parse both formats: take `data:` lines and match the JSON-RPC `id`.
- **Suggestion:** A lightweight, dependency-free reference client for Lambda in the AWS samples would save
  time for teams connecting Bedrock agents to remote MCP servers.
