# G11 — The provider seam with AWS Bedrock (Nova 2 Lite): design (GATE 1)

Recorded 2026-10-12. This is the record of the read-only probes and the design, accepted at GATE 1 **before any seam
code**. The pre-registration is [`g11_PREDICTION.md`](g11_PREDICTION.md).

**Status:** see the Outcome section of [`g11_PREDICTION.md`](g11_PREDICTION.md).

**What G11 is.** The agent path's **generation** runs behind a provider seam, with Amazon Bedrock (Nova 2 Lite) as a
second provider. The two are compared like-for-like on the frozen 28 under pre-registration. Deployment stays on
Render.

**Budget:** at most 5 build-days and $5.00 (Bedrock plus the judge).
- **HARD STOP: 2026-10-17.** GATE 2 is posted by the end of that day; anything unmeasured is recorded NOT TESTED.
- **The pairing:** the date goes with the application send date, 2026-10-24, recorded outside this repository.
  Neither moves independently of the other.

**Frozen:**
- `src/**`: `src/generate.py` is `/ask`'s generator and the v4 byte-repro path;
- `eval/dataset.jsonl`, `run_eval.py`'s metrics, the namespace and k;
- `.github/workflows/ci.yml`;
- the G9 smoke set and snapshot.

**One variable.** The judge stays `gpt-4o-mini` in both arms, and embeddings stay OpenAI, so retrieval is identical
across arms by construction.

**Rows are 1-based.**

**Probe spend:** $0, from local files, the installed packages, read-only LangSmith aggregates, and public AWS and PyPI
pages.

## GATE 1 inputs (2026-10-12)
**The Bedrock arm** is `us.amazon.nova-2-lite-v1:0` in `us-east-1`.
- **The identity:** the converse probe returned "ready" as the least-privilege IAM user `rag-pipeline-bedrock`.
- **Its policy allows, and nothing else:**
  - InvokeModel and InvokeModelWithResponseStream on `foundation-model/anthropic.*` and `amazon.*`;
  - the `us.anthropic.*` and `us.amazon.*` inference-profile ARNs;
  - ListFoundationModels and ListInferenceProfiles.

**Credentials** exist only in the local `.env`, as `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_REGION`.
Their presence was confirmed by name only, and `sts get-caller-identity` confirmed the identity. They are **not**
repository secrets and **not** in `render.yaml`.

**Why Nova and not Claude.** The account's AWS gates, recorded verbatim in the companion file
[`g11_aws_gates.txt`](g11_aws_gates.txt), are quoted below with only the two support-case IDs elided. The doc guard reads
any run of seven or more digits as a commit hash, so the IDs live in the `.txt`, which it does not scan:

> "Anthropic models on this account: Marketplace subscription active (agreement dated 9 Oct 2026), applied on-demand
> quota 0 against a default of 10,000 RPM / 5M TPM; the increase request (cases [two AWS support-case IDs, verbatim in
> g11_aws_gates.txt]) was routed to AWS Sales as above the support threshold — not obtainable inside the time box.
> Amazon Nova models carry their default quotas on this account (Nova 2 Lite: 20 RPM / 8M TPM cross-region). Claude
> Haiku 5.5 is not available to this account tier at all."

**The Bedrock model is a design parameter, not the claim.**

## GATE 1 decisions (accepted 2026-10-12)
1. **Model and region:** `us.amazon.nova-2-lite-v1:0`, `us-east-1`.
2. **HARD STOP:** 2026-10-17, paired with the send date 2026-10-24.
3. **Credentials:** the `.env` keys are `rag-pipeline-bedrock`'s.
4. **The adapter is boto3-only Converse;** numpy is untouched (R2). The `langchain-aws` finding is recorded in
   KNOWN_LIMITATIONS.
5. **P6's instrument:** row 25 under the `/ask` path's unscoped contexts, memoized, both providers, 3 trials each. Plus
   the recorded count of any agent-path refusal across the 28, in either arm.
6. **P3 is non-inferiority on both metrics,** with the per-row rule as written and the one-sided rationale stated.

**Refinements accepted at GATE 1:**
- **A. P7 cannot flip the shipped default inside this PR.**
  - **Every outcome ships the seam with `openai` as the default.**
  - **If the measurement would make Bedrock the default,** that is a stop-and-ask. The switch would change the G9
    smoke snapshot (row 24's generation), Render's configuration and the secrets surface, and each has its own gate.
- **B. "AWS case IDs" joins the doc-guard follow-on line,** alongside run IDs and UUIDs.

**A correction, made at GATE 1.** The packet first proposed fencing the case IDs. The doc guard strips fenced blocks
only for link checks; its hash scan reads every line. Hence the companion `.txt`.

## R1 — The seam, without touching `src/`
**`agent/generation.py`** (new):
- **`build_messages(question, contexts)`**
  - It **imports** `SYSTEM_PROMPT` from `src.generate`, so there is no copy of it.
  - It copies the human message, `Context:\n{block}\n\nQuestion: {question}`, where `block` is the contexts joined by
    `\n\n---\n\n`, or `(no context retrieved)`.
  - A hermetic test asserts it is byte-identical to the messages the frozen `src.generate.generate` sends, so drift on
    either side breaks the build.
- **`generate(question, contexts)`** reads `GENERATION_PROVIDER`: `openai`, the default, or `bedrock`. An unknown value
  raises, as `run_eval.py`'s `PIPELINE` check does.
  - **`openai` delegates to the frozen `src.generate.generate`.** The shipped default is the frozen call itself, not a
    re-implementation: the same `ChatOpenAI(seed=42)` instance, the same traceable span and the same backend
    accumulator.
  - **`bedrock`** calls `generate_with(bedrock_client, question, contexts)`.
- **`generate_with(client, question, contexts)`** is the provider call. It records each call's model id, usage,
  latency, stop reason and request id.

**`agent/graph.py` changes one import:** `from src.generate import generate` becomes `from agent.generation import
generate`. Every existing test that monkeypatches `graph.generate` keeps working.

**`/ask` (`src.pipeline`) stays on OpenAI by design.**

**P1's proof, by test design.**
- **The capture:** a recording fake transport, `httpx.Client.send` patched, records the exact request bytes and
  returns a canned completion.
- **The three calls:** for each of the 28 dataset questions, with synthetic contexts (real ones carry tier-2 text),
  it runs:
  - (a) `src.generate.generate`;
  - (b) `agent.generation.generate` with `GENERATION_PROVIDER=openai`;
  - (c) the graph's `generate_node`.
- **The check:** the three request bodies must be byte-identical, with `model`, `messages`, `temperature: 0` and
  `seed: 42` asserted.

## R2 — Bedrock specifics
**The model.** `us.amazon.nova-2-lite-v1:0` is the US geographic inference profile. Its id carries a version (`v1:0`),
not a date, and it is the only version signal. It is recorded per call as Bedrock's "fingerprint" equivalent.

**What cannot be recorded.**
- **No fingerprint or model version.** The Converse response carries `output`, `stopReason`, `usage` (`inputTokens`,
  `outputTokens`, `totalTokens`, cache counts), `metrics.latencyMs`, `serviceTier`, `performanceConfig` and `trace`.
  It has no model-version or backend-fingerprint field (https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html).
- **No seed.** `inferenceConfig` has `maxTokens`, `temperature`, `topP` and `stopSequences`, but no seed.
- **Recorded instead:** the requested profile id, the AWS request id, `latencyMs`, `stopReason`, `serviceTier` and
  usage.

**The inference settings.**
- `temperature` is 0. Nova 2 allows 0.0–1.0, with a default of 0.7
  (https://docs.aws.amazon.com/nova/latest/nova2-userguide/using-converse-api.html).
- `topP` and `maxTokens` are left unset in both arms; the OpenAI arm sets neither.
- Reasoning stays at its default, disabled.
- Without a seed, Nova promises no determinism. That is a measured difference, not a defect.

**The adapter, and why not `langchain-aws`.**
- **The incompatibility.** The last `langchain-aws` line compatible with `langchain-core==0.3.86` is **0.2.35**
  (2025-10-02). It requires `numpy<2` on Python 3.11. A dry resolve succeeds only by downgrading numpy 2.4.6 → 1.26.4
  across the project, under RAGAS and pandas, which breaks the one-variable rule. The 1.x line needs
  `langchain-core>=1`.
- **The adapter is therefore boto3-only Converse:**
  - `(system, human)` maps to `system=[{text}]` and `messages=[{role: user, content: [{text}]}]`;
  - `inferenceConfig={"temperature": 0}`;
  - it returns the concatenated text blocks, or `""` when there are none.
- **The pins:** `boto3==1.43.103` and `botocore==1.43.103`, uploaded 2026-09-25, at least two weeks old. numpy stays at
  2.4.6.
- **Kept lazy:** `boto3` is imported only when the Bedrock provider is used.

**The Nova 2 Lite list price,** from the AWS Price List API (`AmazonBedrock`, us-east-1, publication
2026-10-10T18:22:42Z):

| tier | input | output | cache read |
|---|--:|--:|--:|
| standard, in-region: the rate a `us.` geographic profile is billed at | **$0.33 / 1M** | **$2.75 / 1M** | $0.0825 / 1M |
| global cross-region | $0.30 / 1M | $2.50 / 1M | $0.075 / 1M |
| `gpt-4o-mini` ([`COST_LEDGER.md`](COST_LEDGER.md)) | $0.15 / 1M | $0.60 / 1M | $0.075 / 1M |

**Tool use.** Nova supports tool use through Converse, but the seam changes generation only (R3).

**Throttling.** Nova 2 Lite's quota on this account is 20 RPM. The eval's client-side limiter keeps Bedrock at 15 RPM
or less, backs off on `ThrottlingException`, and counts throttles.

## R3 — Which LLMs change
**Only generation moves behind the seam.**

| LLM | where | after G11 |
|---|---|---|
| router | `agent.graph._router_llm()`: `gpt-4o-mini`, `json_schema` structured output | stays OpenAI |
| tool decision | `agent.graph._tool_llm()`: `gpt-4o-mini`, `bind_tools` (OpenAI-shaped schemas) | stays OpenAI |
| decomposer | `agent.graph._decomposer_llm()`: `gpt-4o-mini`; the fan-out ships off | stays OpenAI |
| input-guard classifier | `api/guards.py`: `gpt-4o-mini`; the guard ships off | stays OpenAI |
| **generation** | `generate_node` → `src.generate.generate` → `ChatOpenAI(seed=42)` | **behind the seam** |

**No shared instance to split.** Generation and the tool decision already use separate instances. The output guard is
deterministic and uses no LLM.

## R4 — The output guard and citations
**Structured citations** come only from chunk metadata (`api/citations.py`). The answer matters only through emptiness
and `is_refusal()`, so a different inline-citation style cannot change them.

**The real hazard is a paraphrased refusal.**
- `is_refusal` is a whole-answer equality check on the exact refusal sentence.
- A paraphrase would be served at "high" confidence (0.9), with every retrieved page cited (P6).

**Inline markers.** The frozen prompt asks for source_doc_ids drawn from the `[source_doc_id=… page=…]` labels, and
Nova's format may differ. Measured per answer: exact markers, and bare doc-id mentions.

**The output guard** traces numbers.
- G6 recorded 0/84 withholds for `gpt-4o-mini` on the frozen 28 through `/ask/agent`.
- Nova's risk is in-head arithmetic or a unit restatement (the A′ class). Unicode minus signs and thousands separators
  are already normalized.
- Parity is predicted (P5).

## R5 — Baselines (`gpt-4o-mini` generation, agent path)
**Tokens per answer.** Exact and offline: the G1 closure's CAP=0 agent-path contexts and answers for all 28 rows, with
the frozen prompt and `o200k_base`.
- **Input:** p50 **7,505** (range 4,055–11,055).
- **Output:** p50 **92** (range 33–356).

**$ per answer:** p50 **$0.00116**, at uncached list price.
- OpenAI's automatic prompt cache lowers the bill on repeated prompts, so P4 compares both arms at uncached list price,
  with the cached OpenAI figure reported beside it.

**p50 latency.** It is not in the ledger. From LangSmith (read-only, aggregates only), the 100 most recent real
`generate` spans give **1.71 s** (p10 1.14 s, p90 2.43 s).
- **Which spans:** those over 0.3 s, which excludes stubs.
- **Sample bias:** they date from 2026-10-04 and 2026-10-09, and lean toward the large-context rows 10 and 11.
- **The comparison actually used:** P4 measures both arms in the same run.
