"""
ibf_utils : impact-based forecasting (IBF) receptor layer for TITO.

Turns the probabilistic FIM products written by fim_utils (per-cycle
``prob_depth_ge_{tag}.{cycle}.tif`` exceedance grids, or the legacy
``qpeprob.{cycle}.{threshold} meters.tif`` naming) into receptor-level
warning products: every building and road segment in the FIM domain gets
an exceedance probability per depth threshold, a warning class from the
standard flood risk matrix (likelihood x potential impact severity,
Speight et al. 2018, doi:10.1111/jfr3.12281), and administrative units
get exposure summaries and impact warning flags.

The module packages the receptor-preparation logic of the IBF team's
IBFv1.0 Guatemala script (Overture buildings/roads, GHS-BUILT-C
residential classing, dasymetric population from admin census totals,
admin summaries and IWF flags) as reusable, cycle-aware components:

    domain.py     - FIM analysis domain from any product raster
    receptors.py  - preload/clip/tag/cache the receptor base (run once
                    per domain, reused every forecast cycle)
    sampling.py   - probability-product discovery (with correct unit
                    parsing) and receptor sampling
    classify.py   - likelihood bands, severity mapping, risk matrix,
                    admin roll-up and IWF flags
    pipeline_ibf.py - config-driven per-cycle CLI runner

Design rules follow fim_utils: file-based, config-driven (one YAML per
region), agnostic to the number of thresholds and members, no state
between cycles except the receptor cache.

Extra dependencies beyond tito_env: geopandas + pyogrio (vector IO).
Both are declared in tito_env.yml.
"""

__version__ = "0.1.0"

__all__ = [
    "FimDomain",
    "load_ibf_config",
    "prepare_receptors",
    "discover_probability_products",
    "parse_probability_filename",
    "sample_probabilities",
    "classify_features",
    "summarize_admin",
    "run_ibf_cycle",
]

_LAZY = {
    "FimDomain": (".domain", "FimDomain"),
    "load_ibf_config": (".config", "load_ibf_config"),
    "prepare_receptors": (".receptors", "prepare_receptors"),
    "discover_probability_products": (".sampling", "discover_probability_products"),
    "parse_probability_filename": (".sampling", "parse_probability_filename"),
    "sample_probabilities": (".sampling", "sample_probabilities"),
    "classify_features": (".classify", "classify_features"),
    "summarize_admin": (".classify", "summarize_admin"),
    "run_ibf_cycle": (".pipeline_ibf", "run_ibf_cycle"),
}


def __getattr__(name):
    if name in _LAZY:
        import importlib
        module, attr = _LAZY[name]
        return getattr(importlib.import_module(module, __package__), attr)
    raise AttributeError(name)
