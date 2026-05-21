"""Inter-judge agreement: three prompt variants on the same 20-question dataset."""
import json
from itertools import combinations
from pathlib import Path
from sklearn.metrics import cohen_kappa_score

RUN_DIR = Path("artifacts/runs/2026-05-22_multijudge")
ARMS = ["A2_judge_A", "A2_judge_B", "A2_judge_C"]


def load_verdicts(arm: str) -> dict[str, str]:
    out = {}
    for line in (RUN_DIR / arm / "results.jsonl").read_text().splitlines():
        r = json.loads(line)
        v = r.get("eval_verdict")
        if v is None:
            continue
        out[r["question_id"]] = v
    return out


verdicts = {arm: load_verdicts(arm) for arm in ARMS}
qids = sorted(set.intersection(*(set(v.keys()) for v in verdicts.values())))
skipped = sorted(set().union(*(set(v.keys()) for v in verdicts.values())) - set(qids))
if skipped:
    print(f"Skipped (no verdict in >=1 arm): {skipped}\n")

print("Pairwise Cohen kappa:")
for a, b in combinations(ARMS, 2):
    k = cohen_kappa_score([verdicts[a][q] for q in qids], [verdicts[b][q] for q in qids])
    print(f"  {a} vs {b}: kappa = {k:.3f}")

unstable = [q for q in qids if len({verdicts[a][q] for a in ARMS}) > 1]
print(f"\nVerdict-instability: {len(unstable)}/{len(qids)} questions disagree across judges.")

print("\nPer-question votes:")
for q in qids:
    votes = [verdicts[a][q] for a in ARMS]
    majority = max(set(votes), key=votes.count)
    flag = "  <-- UNSTABLE" if len(set(votes)) > 1 else ""
    print(f"  {q}: {votes} -> majority={majority}{flag}")

print("\nCase-study stability (Q012, Q013):")
for q in ("Q012", "Q013"):
    if q in qids:
        votes = [verdicts[a][q] for a in ARMS]
        print(f"  {q}: votes={votes}  stable={len(set(votes)) == 1}")
