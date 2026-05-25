# SENTINEL model + weight research (April 2026)

## Context

User directive: prioritise match quality over pipeline speed. Background
cycle can run an LLM for minutes if the output is better, because the user
applies and reads while it runs.

The prior defaults were hallucinated tags (`gemma4:e4b`, `gemma4:26b` are
not real Ollama releases) and an ancient embedding model
(`all-MiniLM-L6-v2`, 22M params, ~58 MTEB average). The whole stack was
sized for laptop-speed rather than best-available.

## Per-stage recommendations

### Embeddings (agents/match.score_with_embeddings)

Current: `sentence-transformers/all-MiniLM-L6-v2` (22M, 384 dim, ~58 MTEB).

Proposed: `BAAI/bge-m3` (568M, 1024 dim, 8192-token context, multi-function
dense+sparse+colbert).

Why:

a. bge-m3 is the current state-of-the-art open-source retrieval model and
   benches ~67 on MTEB, a ~9-point uplift over MiniLM.
b. 8192-token context means we can feed a whole JD rather than truncating
   at ~200 words, which was a hidden recall killer on long postings.
c. Drop-in replacement via sentence-transformers. No new dependency.

Fallback if model download times out at startup:
`BAAI/bge-large-en-v1.5` (335M, 1024 dim, English-only) - still better than
MiniLM, half the size of bge-m3.

### Resume + job parsing (agents/resume.py, agents/parse.py)

Current: `gemma4:e4b` (invalid tag, falls back to whatever gemma Ollama has).

Proposed: `qwen2.5:14b` (8.7GB VRAM, proven strong on structured JSON
extraction, follows schema instructions reliably).

Why:

a. Qwen 2.5 is the most benchmarked open model for structured-output
   extraction in 2025-26. Handles nested schemas that smaller models drop.
b. 14B gives headroom for messy HTML job cards with boilerplate noise.
c. The alternative `gemma4:9b` (released 2 April 2026) has native
   structured output but is new. Keep it as a future toggle, not a default.

### Match LLM fallback (agents/match.score_with_llm)

Current: `qwen3:8b`.

Proposed: `qwen3:14b` (9GB VRAM, hybrid thinking/non-thinking modes).

Why:

a. Fires only when embeddings are offline, so quality per call matters
   more than throughput. A 14B model is noticeably better at subtle "is
   Platform PM at a bank a fit for a fintech PM candidate" calls.
b. Non-thinking mode keeps latency reasonable; thinking mode kicks in
   when confidence is low.

### Fit-gap / why-this-match (agents/analyzer.py)

Current: `qwen3:8b`.

Proposed: `deepseek-r1:14b` (~9GB VRAM, explicit chain-of-thought).

Why:

a. This stage is the one place a user wants to see the model's reasoning.
   DeepSeek-R1 emits an explicit think trace, which is directly what the
   "Why this match" drill-down displays.
b. Produces 3-5x more tokens than a standard model, but we're caching
   per-role so re-display is free.

### Digest / narrative summary (digest.py)

Current: `gemma4:e4b` (invalid).

Proposed: `gemma3:12b` (8GB VRAM, 128K context, strong narrative prose).

Why:

a. Gemma 3 has the cleanest prose of the current crop. Reads like
   something a careers coach would send, not "the candidate's experience
   is aligned with..."
b. 128K context lets us feed the full matched-roles list, not just a top
   N excerpt.

### Chat tab (core/chat.py)

Current: `qwen3:8b`.

Proposed: `qwen3:14b` (shared with match fallback; one model load).

Why:

a. Conversational Q&A benefits from larger context (we stuff the matches
   table as grounding). Qwen 3's longer context window handles it.
b. Reusing the same model as match fallback means Ollama keeps the
   weights resident and first-token latency stays low.

### Cover letter generation (from Matches detail pane)

Current: config deep tier `gemma4:26b` (invalid).

Proposed: `qwen3:14b`.

Why:

a. Creative-writing benchmarks put qwen3:14b ahead of similar-size
   Llama/Gemma on professional tone and factual adherence.
b. 14B fits comfortably in 16GB VRAM, the 26B hallucinated tag did not.

### Ghost / fake-job detection (core/fake_detector.py)

Keep deterministic. No LLM needed; the rules (age, duplicate titles,
boilerplate matching) are well-tuned and auditable.

### Dimension scoring (core/dimensions.py)

Keep deterministic. The current sub-score maths is transparent and
aligns with human judgement; adding an LLM here only introduces noise.

## Threshold + weight rebalance

### Problem statement

Cycle 1 produced 22 matches at threshold 0.45 out of 191 scored (from 392
ingested). The user asked for hundreds. Three throttles in play:

a. Location hard filter dropped 50% (195/392) before scoring.
b. Threshold 0.45 is tight against a MiniLM score distribution that peaks
   around 0.55 for genuine matches.
c. Salary weight 0.15 pulls scores down on ~60% of listings that lack
   salary data.

### Proposed changes

a. Swap embedding model to bge-m3. Expect the score distribution to shift
   up ~0.05-0.10 and to widen, so top matches separate more cleanly.

b. Change location from hard filter to soft penalty. New `location_mode:
   "soft"` preference (default). In soft mode, LocationFilter stops
   dropping; a new LocationScorer applies a -0.06 penalty when the
   location would have failed, and 0 otherwise. Remote bypasses the
   penalty as before.

c. Drop `match.threshold` from 0.45 to 0.35. Keep the two-tier system
   but tune for bge-m3:
   - embed match: 0.40 (was 0.45)
   - embed maybe: 0.30 (was 0.45)
   - llm match:   0.50 (was 0.50)
   - llm maybe:   0.40 (was 0.50)

d. Reduce salary_weight from 0.15 to 0.08. Salary is one signal among
   many; at 0.15 it was penalising 60% of listings by enough to bump
   them under threshold.

e. Keep years_weight at 0.04. Already small.

f. Keep dimension combine weights (tech 0.5, domain 0.3, sen 0.2).
   They aren't the bottleneck.

### Expected outcome

From 400 ingested:
- Dedupe: ~380 kept.
- Location soft: all 380 scored (instead of 190).
- Score distribution with bge-m3: ~70% clear 0.35 at embed tier.
- Match tier: ~150-200 roles.
- Maybe tier: ~80-120 additional.

That's the "hundreds of roles" the user asked for.

## Hardware notes

Loaded simultaneously, the recommended Ollama stack demands roughly:
- qwen2.5:14b    ~8.7 GB
- qwen3:14b       ~9.0 GB
- deepseek-r1:14b ~9.0 GB
- gemma3:12b      ~8.0 GB

Plus bge-m3 at ~2.3 GB on whichever device sentence-transformers picks.

Ollama swaps models in and out on demand, so only the active stage's
model is resident. Peak VRAM is a single 9 GB model plus the embedding
model (~11-12 GB total), which fits a 16 GB GPU.

On smaller GPUs (8 GB) the stack degrades cleanly: the 14B models spill
to CPU, runs slower but still returns. User's preference: slow-and-good
over fast-and-mediocre.

## Not yet applied

a. Gemma 4 (9B) as an optional structured-extraction model. New release,
   deserves a week of real usage before promoting to default.
b. Embedding via Ollama (`nomic-embed-text`) to drop the
   sentence-transformers dependency. Defer until we package for distribution.
