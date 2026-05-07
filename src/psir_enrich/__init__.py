"""psir_enrich — Enrich OMEGA-PSIR records with WoS UT and PubMed ID."""

__version__ = "0.2.1"

from psir_enrich.core import (
    ArticleState,
    survey_article,
    parse_csl_wos,
    parse_existing_extids,
    norm_doi,
    norm_wos_ut,
    norm_pmid,
    is_meeting_abstract,
    build_extid_element,
    build_patch_xml,
    EXTID_DEFINITIONS,
    NS_URI,
    NS,
)
from psir_enrich.enrich import (
    enrich_one,
    run_enrichment,
    EnrichmentResult,
)
from psir_enrich.wos_client import WosStarterClient

__all__ = [
    "__version__",
    "ArticleState",
    "WosStarterClient",
    "EnrichmentResult",
    "survey_article",
    "parse_csl_wos",
    "parse_existing_extids",
    "norm_doi",
    "norm_wos_ut",
    "norm_pmid",
    "is_meeting_abstract",
    "build_extid_element",
    "build_patch_xml",
    "enrich_one",
    "run_enrichment",
    "EXTID_DEFINITIONS",
    "NS_URI",
    "NS",
]
