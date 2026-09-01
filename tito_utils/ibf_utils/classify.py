"""Classification: likelihood bands, severity axis, risk matrix, admin roll-up.

Two classifications are written side by side:

1. The standard flood risk matrix product (Speight et al. 2018, Fig. 1):
   each severity level of the potential-impact axis is tied to a depth
   threshold, that threshold's exceedance probability gives the
   likelihood band, and the feature's warning class is the worst matrix
   cell across severities. This keeps likelihood and severity as two
   axes instead of collapsing them through one probability cutoff, so a
   low-probability severe case still yields an amber cell.

2. The IBF team's IBFv1.0 fields, for continuity while both products are
   compared: hazard_flag (highest threshold whose probability >= cutoff,
   default 0.3) per feature, res_*/hzrd_* admin breakdowns, IWF flags
   per exposure metric with their published thresholds, and impact_flag.
"""

import numpy as np
import pandas as pd

from .config import (RISK_LEVELS, RISK_COLORS, SEVERITY_LEVELS,
                     LIKELIHOOD_LEVELS)

SEVERITY_ORDER = ["minor", "significant", "severe"]   # severity idx 1, 2, 3


def match_severity_layers(layers, cl_cfg, log=print):
    """Map each configured severity depth to the closest probability layer.

    Returns {severity_name: ProbabilityLayer}. A relative mismatch above
    severity_match_tolerance is reported loudly but still used: with the
    legacy 6in/1ft/2ft product set, 'severe' (0.76 m) matches the 0.6096 m
    grid and the summary records that substitution.
    """
    wanted = cl_cfg["severity_thresholds_m"]
    tol = float(cl_cfg["severity_match_tolerance"])
    out = {}
    for name in SEVERITY_ORDER:
        target = float(wanted[name])
        if not layers:
            break
        best = min(layers, key=lambda l: abs(l.threshold_m - target))
        rel = abs(best.threshold_m - target) / target
        if rel > tol:
            log(f"    ibf_utils.classify: no product within tolerance of "
                f"{name} ({target} m); closest is {best.threshold_m} m - skipped")
            continue
        if rel > 1e-6:
            log(f"    ibf_utils.classify: {name} axis ({target} m) served by "
                f"{best.threshold_m} m product ({best.tag})")
        out[name] = best
    return out


def likelihood_index(p, bands, reporting_threshold):
    """probability -> 0..3 (Very Low..High), or -1 below reporting/zero."""
    if p is None or (isinstance(p, float) and np.isnan(p)) or p < reporting_threshold:
        return -1
    order = ["very_low", "low", "medium", "high"]
    for i, name in enumerate(reversed(order)):
        lo, hi = bands[name]
        if lo <= p < hi:
            return len(order) - 1 - i
    return 3 if p >= bands["high"][0] else -1


def classify_features(gdf, sev_layers, cl_cfg, all_layers=None):
    """Add risk matrix and IBFv1.0-compatible fields to receptors."""
    bands = cl_cfg["likelihood_bands"]
    rep = float(cl_cfg["reporting_threshold"])
    matrix = cl_cfg["matrix"]
    cutoff = float(cl_cfg["hazard_flag_cutoff"])

    out = gdf.copy()
    n = len(out)
    risk = np.zeros(n, dtype=int)
    lik_minor = np.full(n, -1, dtype=int)

    for sev_name, layer in sev_layers.items():
        sev_idx = SEVERITY_ORDER.index(sev_name) + 1
        p = out[layer.field].fillna(0.0).values
        lik = np.array([likelihood_index(x, bands, rep) for x in p])
        cells = np.array([matrix[l][sev_idx] if l >= 0 else 0 for l in lik])
        risk = np.maximum(risk, cells)
        if sev_name == "minor":
            lik_minor = lik

    out["risk_class"] = risk
    out["risk_level"] = [RISK_LEVELS[r] for r in risk]
    out["risk_color"] = [RISK_COLORS[r] for r in risk]
    out["likelihood"] = [LIKELIHOOD_LEVELS[l] if l >= 0 else "none"
                         for l in lik_minor]

    # IBFv1.0 hazard_flag: highest threshold (sorted ascending across ALL
    # probability layers) with p >= cutoff; 0 = below cutoff everywhere.
    layers_sorted = sorted(all_layers or sev_layers.values(),
                           key=lambda l: l.threshold_m)
    flag = np.zeros(n, dtype=int)
    for i, layer in enumerate(layers_sorted, start=1):
        p = out[layer.field].fillna(0.0).values
        flag = np.where(p >= cutoff, i, flag)
    out["hazard_flag"] = flag
    return out


def _wide(df, admin_id, class_col, classes, metrics, prefix):
    """Per-admin per-class metric table with every class forced to exist."""
    if len(df) == 0:
        base = pd.DataFrame({admin_id: []})
    else:
        agg = (df.dropna(subset=[class_col])
                 .groupby([admin_id, class_col])
                 .agg(**{name: (col, op) for name, (col, op) in metrics.items()})
                 .reset_index())
        base = agg.pivot_table(index=admin_id, columns=class_col,
                               values=list(metrics.keys()),
                               fill_value=0, aggfunc="sum")
        cols = pd.MultiIndex.from_product([list(metrics.keys()), classes])
        base = base.reindex(columns=cols, fill_value=0)
        base.columns = [f"{prefix}_{int(c)}_{m}" for m, c in base.columns]
        base = base.reset_index()
    for m in metrics:
        for c in classes:
            col = f"{prefix}_{int(c)}_{m}"
            if col not in base.columns:
                base[col] = 0
    return base


def iwf_flags(summary, iwf_cfg, n_hazard_classes: int):
    """IBFv1.0 Impact Warning Flags: cumulative affected vs absolute or
    percentage thresholds per metric; impact_flag = worst metric."""
    out = summary.copy()
    top = n_hazard_classes - 1
    for iwf_col, m in iwf_cfg.items():
        suffix = m["suffix"]
        total = out[suffix].replace(0, np.nan)
        out[iwf_col] = 0

        def affected(from_class):
            cols = [f"hzrd_{c}_{suffix}" for c in range(from_class, top + 1)]
            cols = [c for c in cols if c in out.columns]
            return out[cols].sum(axis=1) if cols else pd.Series(0.0, index=out.index)

        tiers = [(4, affected(top), m["severe"]),
                 (3, affected(max(top - 1, 1)), m["lmh"]),
                 (2, affected(max(top - 2, 1)), m["lmh"]),
                 (1, affected(1), m["lmh"])]
        for value, aff, thr in tiers:
            hit = (out[iwf_col] == 0) & (
                (aff >= thr["absolute"]) | (aff.div(total) >= thr["percentage"]))
            out.loc[hit, iwf_col] = value
    cols = list(iwf_cfg.keys())
    out["impact_flag"] = out[cols].max(axis=1)
    out[cols + ["impact_flag"]] = out[cols + ["impact_flag"]].astype("int8")
    return out


def summarize_admin(admin, bldgs, roads, cfg, sev_layers):
    """Admin exposure summary + IWF flags + matrix overall risk.

    Baselines (total_pop, bldg_count, bldg_area_m2, rd_len_m, res_*)
    come from the receptor cache's admin layer, where they were computed
    over each unit's FULL receptor stock. Only the hazard exposure
    (hzrd_*) is computed per cycle from the FIM-window receptors, which
    is also the only place probability data exists. IWF percentages
    therefore divide window exposure by full-unit totals, matching the
    semantics of the IBFv1.0 municipality-wide run.
    """
    cl = cfg["classification"]
    adm_spec = cfg["receptors"]["admin"]
    aid = adm_spec["id_field"]
    n_classes = max(int(bldgs["hazard_flag"].max()) + 1 if len(bldgs) else 1,
                    int(roads["hazard_flag"].max()) + 1 if len(roads) else 1, 2)
    hz_classes = list(range(n_classes))

    hz_b = _wide(bldgs, aid, "hazard_flag", hz_classes,
                 {"total_pop": ("population_per_building", "sum"),
                  "bldg_count": ("feature_id", "count"),
                  "bldg_area_m2": ("building_area_m2", "sum")}, "hzrd")
    hz_r = _wide(roads, aid, "hazard_flag", hz_classes,
                 {"rd_len_m": ("road_length_m", "sum")}, "hzrd")

    summary = admin.drop(columns="geometry").copy()
    for extra in (hz_b, hz_r):
        summary = summary.merge(extra, on=aid, how="left")
    num = summary.select_dtypes("number").columns
    summary[num] = summary[num].fillna(0).round(2)

    summary = iwf_flags(summary, cl["iwf"], n_classes)

    # matrix overall risk per unit: worst feature cell in the window
    feat = pd.concat([bldgs[[aid, "risk_class"]], roads[[aid, "risk_class"]]])
    worst = feat.groupby(aid)["risk_class"].max().rename("risk_class")
    summary = summary.merge(worst, on=aid, how="left")
    summary["risk_class"] = summary["risk_class"].fillna(0).astype(int)
    summary["risk_level"] = [RISK_LEVELS[r] for r in summary["risk_class"]]
    summary["risk_color"] = [RISK_COLORS[r] for r in summary["risk_class"]]

    out = admin[[aid, "geometry"]].merge(summary, on=aid, how="left")
    return out
