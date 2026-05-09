# Reproducing the SPARA EIL thesis study (v2)

This document is the minimal recipe a reviewer needs to reproduce the
analysis for the master-thesis study described in `plan-eil-v2.md` and
`experiments/protocol.yaml`.

The experiment is a paired comparison between two arms:

- **A1** — no-evaluator baseline (`EVALUATOR_MODE=off`).
- **A2** — balanced evaluator-in-the-loop (`EVALUATOR_MODE=balanced`,
  `EVALUATOR_MAX_RETRIES=1`, `evaluator_prompt.txt`).

Two optional ablation arms (A3 strict, A4 prompt-B) are gated by
`RUN_OPTIONAL_ARMS=true`.

## Six commands to reproduce

```bash
# 1. Move into the service directory.
cd llm-service

# 2. Set Azure credentials. The runner reads these at start-up.
export AZURE_ENDPOINT="https://<your-resource>.openai.azure.com/"
export OPENAI_API_KEY="<key>"
export OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME="<summariser-deployment>"
export OPENAI_RESPONSE_MODEL_API_VERSION="<api-version>"
# Optional — fall back to the OPENAI_RESPONSE_* values if unset.
export EVALUATOR_MODEL_DEPLOYMENT_NAME="<evaluator-deployment>"
export EVALUATOR_MODEL_API_VERSION="<api-version>"

# 3. Install Python dependencies.
pip install -r requirements.txt

# 4. Run the experiment (A1 then A2 sequentially; cache reused).
bash scripts/run_experiment.sh \
    --dataset src/config/questions.json \
    --run-id repro_$(date +%s)
# To also run A3 and A4: prefix with `RUN_OPTIONAL_ARMS=true`.

# 5. Label the outputs (single annotator, blinded).
python -m scripts.label_outputs \
    --run-dir artifacts/runs/repro_<timestamp> \
    --annotator-id reviewer

# 6. Compute the analysis tables and figures.
python -m scripts.analyze_results \
    --run-dir artifacts/runs/repro_<timestamp>
```

After step 6 the analysis CSVs and figures are written to
`artifacts/runs/<run-id>/analysis/`.

## What gets pinned

`scripts/write_run_record.py` (called automatically at the end of step 4)
records, into `artifacts/runs/<run-id>/run_record.yaml`:

- the SHA-256 of `src/config/questions.json`, both prompt files, and
  `experiments/protocol.yaml` at run time;
- `git rev-parse HEAD` at run start;
- the Azure deployment names and API version actually used;
- per-arm question counts and fail-open counts.

These pins make it possible, given any historical run, to (a) confirm the
input artefacts have not drifted and (b) re-execute deterministically by
checking out the recorded git commit.

## Determinism checks

Two layers protect determinism:

1. **Pinned temperatures.** Both summariser and evaluator run at
   `temperature=0.0` (with a logged warning + automatic fallback if the
   deployment doesn't accept the parameter — see `protocol.yaml`).
2. **Calibration replay.** Re-run only A2 with a fresh `--run-id` and the
   same `--cache-from artifacts/runs/<original>/A1`. Verdict counts must
   match within ±1 entry; otherwise see `plan-eil-v2.md §J.5`.

## Intra-annotator reliability

After at least seven days of cooling-off, the primary annotator relabels
a stratified subset of eight outputs:

```bash
python -m scripts.label_outputs \
    --run-dir artifacts/runs/<run-id> \
    --annotator-id <id> \
    --mode relabel \
    --relabel-subset 8
```

Cohen's kappa values are written to
`artifacts/runs/<run-id>/analysis/intra_annotator_reliability.csv` when
`analyze_results.py` is rerun.

## Scope decisions baked in

The decisions below are recorded in `protocol.yaml.scope_decisions` so
that any departure from them is visible in code review:

- only A1 and A2 are claimed primary; A3/A4 are descriptive at best;
- `aggregated_data` is cached after A1 to factor out upstream pipeline
  noise — this is a methodological choice, not a performance one, and is
  declared in Chapter 3 Methods of the thesis;
- single-annotator design with intra-annotator reliability as the IRR
  proxy.

Anything outside this scope is listed in `plan-eil-v2.md §G — Out of
scope` and flagged as Future Work in the thesis.
