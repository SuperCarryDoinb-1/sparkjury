"""Group badcases into clusters.

Primary method: HDBSCAN (scikit-learn) on unit-normalised embeddings. When scikit-learn is not
installed, or HDBSCAN finds nothing (tiny batches), fall back to deterministic agglomerative
clustering with a cosine-distance threshold so a demo with a handful of badcases still yields
useful groups.
"""

from __future__ import annotations

import math
from typing import Sequence

from sparkjury.cluster.embed import Embedder, cosine
from sparkjury.models.cluster import BadCase, Cluster, Representative


def cluster_badcases(
    badcases: list[BadCase],
    embedder: Embedder,
    *,
    min_cluster_size: int = 3,
    method: str = "auto",              # auto | hdbscan | threshold
    distance_threshold: float = 0.55,  # cosine distance for the threshold method
    n_representatives: int = 3,
) -> tuple[list[Cluster], str]:
    """Assign `cluster_id` on each badcase (in place) and return (clusters, method_used)."""
    if not badcases:
        return [], "none"
    vecs = embedder.embed([b.feature_text for b in badcases])
    labels: list[int] | None = None
    used = method
    if method in ("auto", "hdbscan"):
        labels = _hdbscan(vecs, min_cluster_size)
        used = "hdbscan"
        if labels is None or (method == "auto" and len({l for l in labels if l >= 0}) == 0):
            labels = None
    if labels is None:
        labels = _threshold(vecs, distance_threshold)
        used = "threshold"
    for b, l in zip(badcases, labels):
        b.cluster_id = int(l)
    return _build_clusters(badcases, vecs, n_representatives), used


# ---- methods -----------------------------------------------------------------


def _hdbscan(vecs: list[list[float]], min_cluster_size: int) -> list[int] | None:
    try:
        import numpy as np
        from sklearn.cluster import HDBSCAN
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    if len(vecs) < max(2, min_cluster_size):
        return None
    import warnings

    X = np.asarray(vecs, dtype=float)
    model = HDBSCAN(min_cluster_size=min_cluster_size, min_samples=1, metric="euclidean")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return [int(x) for x in model.fit_predict(X)]


def _threshold(vecs: list[list[float]], threshold: float) -> list[int]:
    """Average-linkage agglomerative clustering, deterministic, O(n^3) but n is small here."""
    n = len(vecs)
    clusters: list[list[int]] = [[i] for i in range(n)]
    dist = [[1.0 - cosine(vecs[i], vecs[j]) for j in range(n)] for i in range(n)]

    def avg(a: list[int], b: list[int]) -> float:
        return sum(dist[i][j] for i in a for j in b) / (len(a) * len(b))

    while len(clusters) > 1:
        best = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = avg(clusters[i], clusters[j])
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None or best[0] > threshold:
            break
        _, i, j = best
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]
    labels = [0] * n
    # singletons become noise (-1); real clusters numbered by first member for determinism
    real = sorted([c for c in clusters if len(c) > 1], key=min)
    for cid, members in enumerate(real):
        for m in members:
            labels[m] = cid
    for c in clusters:
        if len(c) == 1:
            labels[c[0]] = -1
    return labels


# ---- assembling clusters ---------------------------------------------------------


def _build_clusters(badcases: list[BadCase], vecs: list[list[float]], n_rep: int) -> list[Cluster]:
    by_id: dict[int, list[int]] = {}
    for i, b in enumerate(badcases):
        by_id.setdefault(b.cluster_id if b.cluster_id is not None else -1, []).append(i)
    n = len(badcases)
    out: list[Cluster] = []
    for cid, idxs in by_id.items():
        members = [badcases[i] for i in idxs]
        centroid = _centroid([vecs[i] for i in idxs])
        ranked = sorted(idxs, key=lambda i: 1.0 - cosine(vecs[i], centroid))
        reps = [
            Representative(trace_id=badcases[i].trace_id, task_id=badcases[i].task_id,
                           distance=round(1.0 - cosine(vecs[i], centroid), 4), excerpt=badcases[i].excerpt)
            for i in ranked[:n_rep]
        ]
        dim_counts: dict[str, int] = {}
        for b in members:
            for d in b.failed_dimensions:
                dim_counts[d.value] = dim_counts.get(d.value, 0) + 1
        sev = sum(b.severity for b in members) / len(members)
        out.append(Cluster(
            cluster_id=cid, size=len(members), share=len(members) / n, severity=round(sev, 3),
            priority=round(len(members) * sev, 3), member_trace_ids=[b.trace_id for b in members],
            representatives=reps, failed_dimension_counts=dict(sorted(dim_counts.items())),
        ))
    # rank: noise bucket always last; otherwise by priority desc, then size desc
    out.sort(key=lambda c: (c.cluster_id == -1, -c.priority, -c.size, c.cluster_id))
    for r, c in enumerate(out, start=1):
        c.rank = r
    return out


def _centroid(vs: Sequence[Sequence[float]]) -> list[float]:
    dim = len(vs[0])
    c = [sum(v[k] for v in vs) / len(vs) for k in range(dim)]
    norm = math.sqrt(sum(x * x for x in c)) or 1.0
    return [x / norm for x in c]
