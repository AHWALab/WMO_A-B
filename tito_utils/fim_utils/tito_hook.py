"""
TITO orchestrator hook — FIM after the **forecast** EF5 phase only.

Rules:
  - FIM for **90m** (Guatemala, Haiti, Comoros) and **30m** (Antigua, Barbados).
  - Skip **900m** (Guatemala coarse).
  - Only when a forecast phase ran (``run_LR`` / Phase C GFS or StormLab).
  - Pluvial rain = sum of **qpeaccum** components (no qpfaccum / long-range).
  - Non-fatal: never blocks EF5.

Output layout (cycle-first, chain-tagged)::

  outputs/<cycle>/<rkey>/fim/<chain>/
    e.g. outputs/20230621.070000/guatemala_90m/fim/stream_sat_stormlab/
         outputs/20230621.070000/guatemala_90m/fim/imerg_gfs/

Discovers ``fim_config/<Region>*.yaml``. YAMLs with ``hazards:`` → pipeline_pf;
else → pipeline_ensemble.

After a successful FIM site run the IBF receptor layer
(``tito_utils.ibf_utils``) is chained on that site's fresh probability
products when ``fim_config/ibf/<Site>_ibf.yaml`` exists and the region is
enabled via ``ibf_enabled`` / ``ibf_regions`` in the main config. IBF is
non-fatal too: it can never block EF5 or FIM.
"""

from __future__ import annotations

import glob
import os
import re
from typing import Any, Dict, List, Optional, Sequence


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def discover_fim_configs(
    regions: Sequence[str],
    fim_config_dir: str,
) -> List[str]:
    """Return sorted YAML paths for enabled FIM sites matching *regions*."""
    cfg_dir = os.path.abspath(fim_config_dir)
    if not os.path.isdir(cfg_dir):
        return []
    out: List[str] = []
    for region in regions:
        pattern = os.path.join(cfg_dir, f"{region}*.yaml")
        for path in sorted(glob.glob(pattern)):
            path = os.path.abspath(path)
            if os.path.dirname(path) != cfg_dir:
                continue
            try:
                import yaml
                with open(path) as fh:
                    raw = yaml.safe_load(fh) or {}
                if raw.get("enabled") is False:
                    continue
            except Exception:
                continue
            out.append(path)
    return out


def _regions_fim_eligible(
    regions: Sequence[str],
    config: Any,
) -> List[str]:
    """FIM for 90m and 30m (Antigua, Barbados). Skip 900m."""
    try:
        from tito_utils.ef5.jobs.helpers import resolve_region_resolution
    except Exception:
        resolve_region_resolution = None

    model_res = getattr(config, "model_resolution", "90m") if config else "90m"
    rmap = getattr(config, "region_resolution_map", {}) if config else {}
    keep = []
    for r in regions:
        if resolve_region_resolution is not None:
            res = resolve_region_resolution(r, model_res, rmap)
        else:
            res = (rmap or {}).get(r, model_res)
        res_s = str(res).lower().replace(" ", "")
        if res_s in ("90m", "90", "0.09km", "30m", "30", "0.03km"):
            keep.append(r)
    return keep


def _slug(s: str) -> str:
    s = str(s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_") or "unknown"


def forcing_chain_tag(region: str, config: Any = None) -> str:
    """
    Build a short chain id from region_forcing_map, e.g.:
      STREAM_SAT + STORMLAB → stream_sat_stormlab
      IMERG + GFS           → imerg_gfs
    Lists join in order: STREAM_SAT+IMERG / STORMLAB+AROME → stream_sat_imerg_stormlab_arome
    """
    from tito_utils.ef5.jobs.helpers import as_source_list
    fmap = getattr(config, "region_forcing_map", None) or {}
    entry = fmap.get(region) or {}
    qpes = as_source_list(entry.get("qpe_source") or entry.get("qpe") or "")
    qpfs = as_source_list(entry.get("qpf_source") or entry.get("qpf_sources") or "")
    parts = [_slug(x) for x in qpes + qpfs if x]
    return "_".join(parts) if parts else "unknown"


def _region_key(region: str, config: Any = None) -> str:
    try:
        from tito_utils.ef5.jobs.helpers import (
            region_path_key,
            resolve_region_resolution,
        )
        model_res = getattr(config, "model_resolution", "90m") if config else "90m"
        rmap = getattr(config, "region_resolution_map", {}) if config else {}
        res = resolve_region_resolution(region, model_res, rmap)
        return region_path_key(region, res)
    except Exception:
        res = "90m"
        if config is not None:
            res = (getattr(config, "region_resolution_map", {}) or {}).get(
                region, getattr(config, "model_resolution", "90m"))
        return f"{region.lower()}_{res}"


def _match_yaml_to_region(yml_basename: str, regions: Sequence[str]) -> Optional[str]:
    """Pick the longest region name that is a prefix of the yaml basename."""
    best = None
    for r in regions:
        if yml_basename.startswith(r) and (best is None or len(r) > len(best)):
            best = r
    return best


def _region_fim_entry(region: str, config: Any) -> Optional[dict]:
    """The region's entry in config.fim_regions, normalized to a dict.

    Accepts the bool shorthand {"Region": True/False}. Returns None when the
    region is not listed at all (caller treats that as enabled, YAML rules).
    """
    reg_map = (getattr(config, "fim_regions", None) or {}) if config is not None else {}
    if region not in reg_map:
        return None
    entry = reg_map.get(region)
    if not isinstance(entry, dict):
        entry = {"enabled": bool(entry)}
    return entry


def _region_thresholds(region: str, config: Any) -> Optional[List[float]]:
    """Cleaned thresholds_m for *region* from config.fim_regions, or None."""
    entry = _region_fim_entry(region, config)
    if not entry:
        return None
    thr = entry.get("thresholds_m")
    if not thr:
        return None
    try:
        clean = sorted({float(t) for t in thr if float(t) > 0})
    except (TypeError, ValueError):
        return None
    return clean or None


def _ibf_master_enabled(config: Any) -> bool:
    """Master IBF switch (config.ibf_enabled, default True)."""
    if config is None:
        return True
    return bool(getattr(config, "ibf_enabled", True))


def _region_ibf_entry(region: str, config: Any) -> Optional[dict]:
    """The region's entry in config.ibf_regions, normalized like fim_regions.

    Accepts the bool shorthand {"Region": True/False}. Returns None when the
    region is not listed at all (caller treats that as enabled, YAML rules).
    """
    reg_map = (getattr(config, "ibf_regions", None) or {}) if config is not None else {}
    if region not in reg_map:
        return None
    entry = reg_map.get(region)
    if not isinstance(entry, dict):
        entry = {"enabled": bool(entry)}
    return entry


def run_ibf_for_site(
    *,
    site_stem: str,
    region: str,
    products_root: str,
    cycle: str,
    cfg_dir: str,
    root: str,
    config: Any = None,
    master_log: Any = None,
    verbose: bool = True,
) -> Optional[dict]:
    """
    IBF receptor products for one FIM site, right after its FIM run.

    Chained but never required: every problem is caught and returned as a
    summary dict; EF5 and FIM products are never affected.

    Config surface (Caribbean_Comoros_config.py):
      ibf_enabled              master switch, default True
      ibf_regions[region]      enabled switch plus optional overrides
                               (severity_thresholds_m, hazard_flag_cutoff,
                               reporting_threshold), same shape as fim_regions

    Site YAML: fim_config/ibf/<Site>_ibf.yaml. No YAML means no IBF for the
    site, silently, unless the region is explicitly enabled in ibf_regions.
    """
    log = print if verbose else (lambda *a, **k: None)

    if not _ibf_master_enabled(config):
        return None

    entry = _region_ibf_entry(region, config)
    if entry is not None and not entry.get("enabled", True):
        return None

    ibf_yml = os.path.join(cfg_dir, "ibf", f"{site_stem}_ibf.yaml")
    if not os.path.isfile(ibf_yml):
        if entry is not None and entry.get("enabled", True):
            log(f"  IBF: {site_stem}: no fim_config/ibf/{site_stem}_ibf.yaml, skip")
        return None

    try:
        from tito_utils.ibf_utils.config import load_ibf_config
        from tito_utils.ibf_utils.pipeline_ibf import run_ibf_cycle
    except Exception as exc:
        log(f"  IBF: skipped, dependencies missing ({exc}); "
            "geopandas and pyogrio are declared in tito_env.yml")
        if master_log:
            master_log.info("IBF skipped for %s: deps missing (%s)", site_stem, exc)
        return {"region": region, "site": site_stem, "status": "deps_missing"}

    cfg: dict = {}
    try:
        cfg = load_ibf_config(ibf_yml, root=root)

        # Overrides from the main config (ibf_regions), applied on top of the
        # site YAML so operators never have to open the YAML for these.
        cl = cfg.setdefault("classification", {})
        applied = []
        if entry:
            sev = entry.get("severity_thresholds_m")
            if isinstance(sev, dict) and sev:
                cl["severity_thresholds_m"] = {
                    str(k): float(v) for k, v in sev.items()}
                applied.append(
                    f"severity_thresholds_m={cl['severity_thresholds_m']}")
            for key in ("hazard_flag_cutoff", "reporting_threshold"):
                if entry.get(key) is not None:
                    cl[key] = float(entry[key])
                    applied.append(f"{key}={cl[key]}")
        if applied:
            log(f"       IBF overrides from ibf_regions: {', '.join(applied)}")

        # Cycle-first, next to FIM:
        #   outputs/<cycle>/<rkey>/ibf/<Site>/
        rkey = _region_key(region, config)
        data_root = getattr(config, "dataPath", "outputs/") if config else "outputs/"
        if not os.path.isabs(data_root):
            data_root = os.path.join(root, str(data_root).rstrip("/\\"))
        ibf_out = os.path.join(data_root, cycle, rkey, "ibf", site_stem)
        cfg.setdefault("outputs", {})
        cfg["outputs"]["root"] = ibf_out
        cfg["outputs"]["append_cycle"] = False

        mode = (cfg.get("fim_products") or {}).get("mode", "combined")
        products_dir = os.path.join(products_root, mode)
        if not os.path.isabs(products_dir):
            # same resolution rule as pipeline_pf: relative paths live
            # under the repo root, independent of the current directory
            products_dir = os.path.join(root, products_dir)
        log(f"  IBF: running {site_stem} on {products_dir} ...")
        if master_log:
            master_log.info(
                "IBF start %s cycle=%s dir=%s", site_stem, cycle, products_dir)

        summary = run_ibf_cycle(
            cfg, cycle=cycle, products_dir=products_dir, verbose=verbose)
        status = summary.get("status", "?") if isinstance(summary, dict) else "?"
        log(f"  IBF: {site_stem} -> {status}")
        if master_log:
            master_log.info("IBF done %s status=%s", site_stem, status)
        return summary
    except Exception as exc:
        # A missing receptor preload is the usual cause on fresh machines.
        hint = ""
        try:
            src = ((cfg.get("receptors") or {}).get("buildings") or {}).get(
                "source", "")
            if src:
                from tito_utils.ibf_utils.config import resolve as _ibf_resolve
                if not os.path.exists(_ibf_resolve(cfg, src)):
                    hint = (f" (receptor preload not found at {src}; "
                            "see tito_utils/ibf_utils/README.md)")
        except Exception:
            pass
        log(f"  IBF: {site_stem} failed (non-fatal): {exc}{hint}")
        if master_log:
            master_log.error("IBF failed %s: %s", site_stem, exc)
        return {"region": region, "site": site_stem, "status": "error",
                "error": str(exc)}


# Path templates for cycle-first EF5 layout, keyed by forcing_chain_tag().
# Applied at runtime so one site YAML works for both training chains.
_CHAIN_TEMPLATES = {
    "stream_sat_stormlab": {
        "member": "{cycle}/{rkey}/stormlab/ensOut{ens}_sl{sl}",
        "rain_components": [
            {"name": "stream_sat", "template": "{cycle}/{rkey}/stream_sat/ensOut{ens}",
             "grid": "qpe_accum", "required": True},
            {"name": "stormlab", "template": "{cycle}/{rkey}/stormlab/ensOut{ens}_sl{sl}",
             "grid": "qpe_accum", "required": True},
        ],
        "trigger_sources": [
            {"template": "{cycle}/{rkey}/stream_sat/ensOut{ens}"},
            {"template": "{cycle}/{rkey}/stormlab/ensOut{ens}_sl{sl}"},
        ],
    },
    "imerg_gfs": {
        "member": "{cycle}/{rkey}/gfs",
        "rain_components": [
            {"name": "imerg", "template": "{cycle}/{rkey}/imerg",
             "grid": "qpe_accum", "required": True},
            {"name": "scampr_gap", "template": "{cycle}/{rkey}/scampr_det",
             "grid": "qpe_accum", "required": False},
            {"name": "gfs_forecast", "template": "{cycle}/{rkey}/gfs",
             "grid": "qpe_accum", "required": True},
        ],
        "trigger_sources": [
            {"template": "{cycle}/{rkey}/gfs"},
            {"template": "{cycle}/{rkey}/imerg"},
        ],
    },
    "imerg_stormlab": {
        "member": "{cycle}/{rkey}/stormlab/ensOut{ens}_sl{sl}",
        "rain_components": [
            {"name": "imerg", "template": "{cycle}/{rkey}/imerg",
             "grid": "qpe_accum", "required": True},
            {"name": "scampr_gap", "template": "{cycle}/{rkey}/scampr_det",
             "grid": "qpe_accum", "required": False},
            {"name": "stormlab", "template": "{cycle}/{rkey}/stormlab/ensOut{ens}_sl{sl}",
             "grid": "qpe_accum", "required": True},
        ],
        "trigger_sources": [
            {"template": "{cycle}/{rkey}/stormlab/ensOut{ens}_sl{sl}"},
            {"template": "{cycle}/{rkey}/imerg"},
        ],
    },
    "stream_sat_gfs": {
        "member": "{cycle}/{rkey}/gfs/ensOut{ens}",
        "rain_components": [
            {"name": "stream_sat", "template": "{cycle}/{rkey}/stream_sat/ensOut{ens}",
             "grid": "qpe_accum", "required": True},
            {"name": "gfs_forecast", "template": "{cycle}/{rkey}/gfs/ensOut{ens}",
             "grid": "qpe_accum", "required": True},
        ],
        "trigger_sources": [
            {"template": "{cycle}/{rkey}/gfs/ensOut{ens}"},
            {"template": "{cycle}/{rkey}/stream_sat/ensOut{ens}"},
        ],
    },
    "stream_sat_arome": {
        "member": "{cycle}/{rkey}/arome/ensOut{ens}",
        "rain_components": [
            {"name": "stream_sat", "template": "{cycle}/{rkey}/stream_sat/ensOut{ens}",
             "grid": "qpe_accum", "required": True},
            {"name": "arome_forecast", "template": "{cycle}/{rkey}/arome/ensOut{ens}",
             "grid": "qpe_accum", "required": True},
        ],
        "trigger_sources": [
            {"template": "{cycle}/{rkey}/arome/ensOut{ens}"},
            {"template": "{cycle}/{rkey}/stream_sat/ensOut{ens}"},
        ],
    },
    "imerg_arome": {
        "member": "{cycle}/{rkey}/arome",
        "rain_components": [
            {"name": "imerg", "template": "{cycle}/{rkey}/imerg",
             "grid": "qpe_accum", "required": True},
            {"name": "scampr_gap", "template": "{cycle}/{rkey}/scampr_det",
             "grid": "qpe_accum", "required": False},
            {"name": "arome_forecast", "template": "{cycle}/{rkey}/arome",
             "grid": "qpe_accum", "required": True},
        ],
        "trigger_sources": [
            {"template": "{cycle}/{rkey}/arome"},
            {"template": "{cycle}/{rkey}/imerg"},
        ],
    },
}


def _apply_chain_templates(cfg: dict, chain: str) -> None:
    """Override member / rain / trigger paths for the active forcing chain."""
    spec = _CHAIN_TEMPLATES.get(chain)
    if not spec:
        return
    cfg.setdefault("member", {})
    cfg["member"]["template"] = spec["member"]
    cfg["rain_components"] = [dict(x) for x in spec["rain_components"]]
    cfg.setdefault("trigger", {})
    cfg["trigger"]["sources"] = [dict(x) for x in spec["trigger_sources"]]


def _explain_no_runs(outputs_root: str, template: str, cycle: str, chain: str) -> str:
    """Human-readable reason when discover finds zero members."""
    import glob as _glob
    tmpl = (template or "").strip("/").replace("\\", "/")
    if "{cycle}" in tmpl and cycle:
        search = tmpl.replace("{cycle}", cycle)
    else:
        search = tmpl
    # freeze known placeholders for a concrete glob hint
    hint = search
    for ph in ("{rkey}", "{ens}", "{sl}", "{qpf}"):
        hint = hint.replace(ph, "*")
    pattern = os.path.join(outputs_root, hint)
    hits = _glob.glob(pattern)
    # also list what exists under cycle/
    cycle_dir = os.path.join(outputs_root, cycle) if cycle else outputs_root
    kids = []
    if os.path.isdir(cycle_dir):
        for r in sorted(os.listdir(cycle_dir))[:8]:
            rp = os.path.join(cycle_dir, r)
            if os.path.isdir(rp):
                prods = [p for p in sorted(os.listdir(rp))[:12] if os.path.isdir(os.path.join(rp, p))]
                kids.append(f"{r}/[{', '.join(prods)}]")
    lines = [
        f"no EF5 member folders matched chain={chain}",
        f"  expected template: {template}",
        f"  glob tried:        {pattern}",
        f"  matches:           {len(hits)}",
    ]
    if kids:
        lines.append(f"  under outputs/{cycle}/: " + "; ".join(kids))
    else:
        lines.append(f"  under outputs/{cycle}/: (missing or empty)")
    lines.append(
        "  tip: FIM needs the forecast folder for this chain "
        "(imerg_gfs → …/gfs/; stream_sat_stormlab → …/stormlab/ensOut*_sl*/)"
    )
    return "\n".join(lines)


def run_fim_for_cycle(
    *,
    regions_to_run: Sequence[str],
    cycle: str,
    config: Any = None,
    master_log: Any = None,
    verbose: bool = True,
    forecast_ran: bool = True,
    region_qpe_sources: Optional[Dict[str, str]] = None,
    region_qpf_sources: Optional[Dict[str, Sequence[str]]] = None,
) -> List[dict]:
    """
    Run FIM after forecast EF5 finishes one cycle.

    Parameters
    ----------
    forecast_ran
        Must be True (Phase C / run_LR produced forecast QPE runs). Otherwise skip.
    region_qpe_sources / region_qpf_sources
        Optional explicit maps; otherwise read from config.region_forcing_map.
    """
    log = print if verbose else (lambda *a, **k: None)

    enabled = True
    if config is not None:
        enabled = bool(getattr(config, "fim_enabled", True))
    if not enabled:
        log("  FIM: disabled (fim_enabled=False)")
        if master_log:
            master_log.info("FIM disabled via fim_enabled=False")
        return []

    if not forecast_ran:
        log("  FIM: skipped (no forecast phase this cycle)")
        if master_log:
            master_log.info("FIM skipped — forecast_ran=False")
        return []

    regions_90 = _regions_fim_eligible(regions_to_run, config)
    skipped = [r for r in regions_to_run if r not in regions_90]
    if skipped:
        log(f"  FIM: skip 900m / ineligible regions: {', '.join(skipped)}")
        if master_log:
            master_log.info("FIM skip ineligible: %s", skipped)
    if not regions_90:
        log("  FIM: no eligible regions (90m or 30m) — skip")
        if master_log:
            master_log.info("FIM skipped — no eligible regions")
        return []

    # Per-region switches from the main config (fim_regions block), v0.5.
    kept = []
    for r in regions_90:
        entry = _region_fim_entry(r, config)
        if entry is not None and not entry.get("enabled", True):
            log(f"  FIM: {r} disabled in the main config (fim_regions)")
            if master_log:
                master_log.info("FIM %s disabled via fim_regions", r)
            continue
        kept.append(r)
    regions_90 = kept
    if not regions_90:
        log("  FIM: every region is switched off in fim_regions")
        if master_log:
            master_log.info("FIM skipped: all regions off in fim_regions")
        return []

    root = _project_root()
    if config is not None and getattr(config, "fim_root", None):
        root = os.path.abspath(getattr(config, "fim_root"))

    cfg_dir = os.path.join(root, "fim_config")
    if config is not None and getattr(config, "fim_config_dir", None):
        d = getattr(config, "fim_config_dir")
        cfg_dir = d if os.path.isabs(d) else os.path.join(root, d)

    yaml_paths = discover_fim_configs(regions_90, cfg_dir)
    if not yaml_paths:
        log(f"  FIM: no site configs under {cfg_dir} for {regions_90}")
        if master_log:
            master_log.info("FIM: no configs for %s in %s", regions_90, cfg_dir)
        return []

    os.environ.setdefault("TITO_FIM_ROOT", root)
    data_root = getattr(config, "dataPath", "outputs/") if config else "outputs/"
    summaries: List[dict] = []

    for yml in yaml_paths:
        site = os.path.basename(yml)
        site_stem = os.path.splitext(site)[0]
        region = _match_yaml_to_region(site_stem, regions_90) or regions_90[0]
        rkey = _region_key(region, config)
        try:
            import yaml as _yaml
            with open(yml) as _fh:
                _raw = _yaml.safe_load(_fh) or {}
            req = str(_raw.get("required_resolution") or "").lower().replace(" ", "")
        except Exception:
            req = ""
        if req:
            actual = rkey.rsplit("_", 1)[-1].lower().replace(" ", "")
            if req not in (actual, actual.replace("m", ""), f"0.0{actual.replace('m','')}km"):
                log(f"  FIM: skip {site} (required_resolution={req}, run is {actual})")
                continue

        from tito_utils.ef5.jobs.helpers import as_source_list
        if region_qpe_sources is not None or region_qpf_sources is not None:
            qpes = as_source_list((region_qpe_sources or {}).get(region, ""))
            qpfs = as_source_list((region_qpf_sources or {}).get(region, []))
        else:
            fmap = getattr(config, "region_forcing_map", None) or {}
            entry = fmap.get(region) or {}
            qpes = as_source_list(entry.get("qpe_source") or entry.get("qpe") or "")
            qpfs = as_source_list(
                entry.get("qpf_source") or entry.get("qpf_sources") or "")
        if not qpes:
            qpes = [""]
        chains = (
            [f"{_slug(p)}_{_slug(f)}" for p in qpes for f in qpfs]
            if qpfs else [_slug(p) or "unknown" for p in qpes]
        )

        chain = chains[0] if chains else "unknown"
        try:
          for chain in chains:
            products_root = os.path.join(
                data_root.rstrip("/\\"), cycle, rkey, "fim", chain)
            import yaml
            with open(yml) as fh:
                raw = yaml.safe_load(fh) or {}
            has_hazards = isinstance(raw.get("hazards"), dict)
            log(
                f"  FIM: running {site} after forecast "
                f"(cycle={cycle}, {rkey}, chain={chain}, "
                f"{'P+F' if has_hazards else 'ensemble'}) …"
            )
            log(f"       products → {products_root}")
            if master_log:
                master_log.info(
                    "FIM start %s cycle=%s chain=%s out=%s",
                    site, cycle, chain, products_root)

            if has_hazards:
                from .pipeline_pf import load_pf_config, run_pf_cycle
                cfg = load_pf_config(yml, root=root)
                cfg["outputs_root"] = (
                    data_root if os.path.isabs(data_root)
                    else os.path.join(root, str(data_root).rstrip("/\\"))
                )
                cfg["products_root"] = products_root
                cfg["append_cycle"] = False
                _thr = _region_thresholds(region, config)
                if _thr:
                    cfg["thresholds_m"] = _thr
                    log(f"       thresholds from fim_regions: {_thr}")
                    if master_log:
                        master_log.info(
                            "FIM %s thresholds from fim_regions: %s", site, _thr)
                _apply_chain_templates(cfg, chain)
                log(f"       member template: {cfg.get('member', {}).get('template')}")
                summary = run_pf_cycle(cfg, cycle=cycle, verbose=verbose)
            else:
                from .pipeline_ensemble import load_ensemble_config, run_ensemble_cycle
                cfg = load_ensemble_config(yml, root=root)
                cfg["outputs_root"] = (
                    data_root if os.path.isabs(data_root)
                    else os.path.join(root, str(data_root).rstrip("/\\"))
                )
                cfg["products_root"] = products_root
                cfg["append_cycle"] = False
                _apply_chain_templates(cfg, chain)
                log(f"       member template: {cfg.get('member', {}).get('template')}")
                summary = run_ensemble_cycle(cfg, cycle=cycle, verbose=verbose)

            status = summary.get("status", "?")
            summary["chain"] = chain
            summary["products_root"] = products_root
            if status == "no_runs":
                outputs_root = cfg.get("outputs_root", "outputs")
                if not os.path.isabs(outputs_root):
                    outputs_root = os.path.join(root, outputs_root)
                detail = _explain_no_runs(
                    outputs_root,
                    (cfg.get("member") or {}).get("template", ""),
                    cycle,
                    chain,
                )
                summary["detail"] = detail
                log(f"  FIM: {site} [{chain}] → no_runs")
                for line in detail.splitlines():
                    log(f"  FIM: {line}")
            else:
                log(f"  FIM: {site} [{chain}] → {status}")
            if master_log:
                master_log.info(
                    "FIM done %s chain=%s status=%s", site, chain, status)

            # IBF receptor products, chained on this site's fresh FIM
            # probabilities (config gated; see run_ibf_for_site).
            if status not in ("error", "no_runs", "quiet"):
                ibf_summary = run_ibf_for_site(
                    site_stem=site_stem,
                    region=region,
                    products_root=products_root,
                    cycle=cycle,
                    cfg_dir=cfg_dir,
                    root=root,
                    config=config,
                    master_log=master_log,
                    verbose=verbose,
                )
                if ibf_summary:
                    summary["ibf"] = ibf_summary

            summaries.append(summary)
        except Exception as exc:
            msg = f"  FIM: {site} failed (non-fatal): {exc}"
            log(msg)
            if master_log:
                master_log.error("FIM failed %s: %s", site, exc)
            summaries.append({
                "config": yml, "cycle": cycle, "status": "error",
                "chain": chain, "error": str(exc),
            })

    return summaries
