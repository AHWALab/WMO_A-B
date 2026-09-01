"""Per-cycle IBF runner.

    python -m tito_utils.ibf_utils.pipeline_ibf --config fim_config/ibf/<region>_ibf.yaml \
        [--cycle 20230621.070000] [--products-dir DIR] [--rebuild-cache]

Steps per cycle:
    1. discover the cycle's probability products (new or legacy naming;
       other-cycle files in the folder are reported, never blended)
    2. build or reuse the receptor cache for the product domain
    3. sample every probability grid onto buildings and roads (max)
    4. classify: risk matrix + IBFv1.0-compatible hazard/IWF fields
    5. write {outputs.root}/{cycle}/ibf_receptors.{cycle}.gpkg
             (buildings_ibf / roads_ibf / admin_ibf),
             ibf_admin_summary.{cycle}.csv, ibf_summary.{cycle}.json
"""

import json
import os
import sys

from .config import load_ibf_config, resolve, RISK_LEVELS
from .domain import FimDomain
from .receptors import prepare_receptors, write_gpkg_layers
from .sampling import discover_probability_products, sample_probabilities
from .classify import match_severity_layers, classify_features, summarize_admin


def run_ibf_cycle(cfg, cycle: str = None, products_dir: str = None,
                  rebuild_cache: bool = False, verbose: bool = True) -> dict:
    if isinstance(cfg, str):
        cfg = load_ibf_config(cfg)
    log = print if verbose else (lambda *a, **k: None)

    fp = cfg["fim_products"]
    directory = products_dir or resolve(
        cfg, fp["root"].format(cycle=cycle or "", mode=fp["mode"],
                               region=cfg["region"]).rstrip("/\\"))
    layers, skipped, cycle = discover_probability_products(
        directory, cycle=cycle, prefer_overbank=fp["prefer_overbank"])
    if not layers:
        log(f"  IBF {cfg['region']}: no probability products in {directory}")
        return {"region": cfg["region"], "cycle": cycle, "status": "no_products"}
    log(f"  IBF {cfg['region']} {cycle}: {len(layers)} probability grids "
        f"({', '.join(l.tag for l in layers)})")
    for s in skipped:
        log(f"    skipped other-cycle file: {os.path.basename(s.path)} ({s.cycle})")

    domain = FimDomain.from_raster(layers[0].path)
    receptors, manifest_path = prepare_receptors(
        cfg, domain, rebuild=rebuild_cache, verbose=verbose)

    sev_layers = match_severity_layers(layers, cfg["classification"], log=log)
    if not sev_layers:
        return {"region": cfg["region"], "cycle": cycle,
                "status": "no_severity_match"}

    bldgs = sample_probabilities(receptors["buildings"], layers)
    roads = sample_probabilities(receptors["roads"], layers)
    bldgs = classify_features(bldgs, sev_layers, cfg["classification"], layers)
    roads = classify_features(roads, sev_layers, cfg["classification"], layers)
    admin = summarize_admin(receptors["admin"], bldgs, roads, cfg, sev_layers)

    out_root = resolve(cfg, cfg["outputs"]["root"].format(region=cfg["region"]))
    out_dir = os.path.join(out_root, cycle) if cfg["outputs"]["append_cycle"] else out_root
    os.makedirs(out_dir, exist_ok=True)
    gpkg = os.path.join(out_dir, f"ibf_receptors.{cycle}.gpkg")
    write_gpkg_layers(gpkg, (
        ("buildings_ibf", bldgs),
        ("roads_ibf", roads),
        ("admin_ibf", admin),
    ))
    admin.drop(columns="geometry").to_csv(
        os.path.join(out_dir, f"ibf_admin_summary.{cycle}.csv"), index=False)

    def by_risk(df, col="risk_class"):
        return {RISK_LEVELS[r]: int((df[col] == r).sum()) for r in range(4)}

    summary = {
        "region": cfg["region"], "cycle": cycle, "status": "ok",
        "products_dir": directory,
        "layers": [{"tag": l.tag, "threshold_m": l.threshold_m,
                    "file": os.path.basename(l.path), "cycle": l.cycle,
                    "overbank": l.overbank} for l in layers],
        "skipped_other_cycle": [os.path.basename(s.path) for s in skipped],
        "severity_axis": {name: {"target_m": cfg["classification"]
                                 ["severity_thresholds_m"][name],
                                 "layer_tag": l.tag,
                                 "layer_threshold_m": l.threshold_m}
                          for name, l in sev_layers.items()},
        "receptor_manifest": manifest_path,
        "buildings_by_risk": by_risk(bldgs),
        "roads_by_risk": by_risk(roads),
        "admin_by_risk": by_risk(admin),
        "population_at_yellow_or_worse": float(
            bldgs.loc[bldgs["risk_class"] >= 1, "population_per_building"].sum()),
        "config_echo": {k: cfg[k] for k in
                        ("classification", "fim_products", "outputs")},
        "files": [os.path.basename(gpkg),
                  f"ibf_admin_summary.{cycle}.csv"],
    }
    with open(os.path.join(out_dir, f"ibf_summary.{cycle}.json"), "w") as fh:
        json.dump(summary, fh, indent=2, default=str)
    log(f"    IBF products -> {out_dir}")
    log(f"    buildings by risk: {summary['buildings_by_risk']}")
    return summary


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Run one IBF receptor cycle")
    ap.add_argument("--config", required=True)
    ap.add_argument("--cycle", default=None)
    ap.add_argument("--root", default=None)
    ap.add_argument("--products-dir", default=None,
                    help="explicit folder with the cycle's probability rasters")
    ap.add_argument("--rebuild-cache", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    cfg = load_ibf_config(a.config, root=a.root)
    s = run_ibf_cycle(cfg, cycle=a.cycle, products_dir=a.products_dir,
                      rebuild_cache=a.rebuild_cache, verbose=not a.quiet)
    return 0 if s.get("status") == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
