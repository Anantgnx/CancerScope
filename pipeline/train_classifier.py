"""Phase A step 3-4: logistic regression on UNI2-h PCam features.

    python -m pipeline.train_classifier [--train train --valid valid --test test]

Picks regularization C on valid (AUC), saves models/trained/tumor_classifier.pt,
writes outputs/eval/pcam_metrics.json (test accuracy/AUC = the headline number).
"""
import argparse
import json
import time

import numpy as np
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, balanced_accuracy_score, roc_auc_score

import config

C_GRID = [0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0]


def load(tag):
    d = np.load(config.PCAM_FEATURES_DIR / f"{tag}.npz")
    return d["features"].astype(np.float32), d["labels"].astype(int)


def evaluate(clf, x, y):
    p = clf.predict_proba(x)[:, 1]
    pred = p > 0.5
    return {"n": int(len(y)), "accuracy": round(accuracy_score(y, pred), 4),
            "balanced_accuracy": round(balanced_accuracy_score(y, pred), 4),
            "auc": round(roc_auc_score(y, p), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="train")
    ap.add_argument("--valid", default="valid")
    ap.add_argument("--test", default="test")
    args = ap.parse_args()

    xtr, ytr = load(args.train)
    xva, yva = load(args.valid)
    mean, std = xtr.mean(0), xtr.std(0) + 1e-6
    norm = lambda x: (x - mean) / std  # noqa: E731

    best = None
    for c in C_GRID:
        t0 = time.perf_counter()
        clf = LogisticRegression(C=c, max_iter=2000).fit(norm(xtr), ytr)
        m = evaluate(clf, norm(xva), yva)
        print(f"C={c:<6} valid auc {m['auc']:.4f} acc {m['accuracy']:.4f}  ({time.perf_counter() - t0:.1f}s)")
        if best is None or m["auc"] > best[1]["auc"]:
            best = (c, m, clf)
    c, valid_metrics, clf = best
    print(f"best C={c}")

    results = {"model": "UNI2-h + logistic regression", "C": c,
               "train": {"n": int(len(ytr))}, "valid": valid_metrics}
    if (config.PCAM_FEATURES_DIR / f"{args.test}.npz").exists():
        xte, yte = load(args.test)
        results["test"] = evaluate(clf, norm(xte), yte)
        print(f"TEST: {results['test']}")
    else:
        print(f"no {args.test}.npz yet; re-run after extracting test features for the headline number")

    config.TRAINED_DIR.mkdir(parents=True, exist_ok=True)
    torch.save({
        "weight": torch.tensor(clf.coef_[0], dtype=torch.float32),
        "bias": torch.tensor(clf.intercept_[0], dtype=torch.float32),
        "mean": torch.from_numpy(mean), "std": torch.from_numpy(std),
        "feature_model": "UNI2-h", "C": c, "train_n": int(len(ytr)),
    }, config.CLASSIFIER_PATH)
    eval_dir = config.ROOT / "outputs" / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "pcam_metrics.json").write_text(json.dumps(results, indent=2))
    print(f"saved {config.CLASSIFIER_PATH} and {eval_dir / 'pcam_metrics.json'}")


if __name__ == "__main__":
    main()
