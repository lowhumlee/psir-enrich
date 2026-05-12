"""psir_enrich — Enrich OMEGA-PSIR records with WoS UT, PubMed ID,
and full metadata via the Clarivate WoS Starter and Expanded APIs."""

__version__ = "0.4.0"

from psir_enrich.core import (
    ArticleState,
    survey_article,
    parse_csl_wos,
    parse_csl_pmid,
    parse_existing_extids,
    norm_doi,
    norm_wos_ut,
    norm_pmid,
    is_meeting_abstract,
    build_full_output_xml,
    inject_expanded_metadata,
    EXTID_DEFINITIONS,
    NS_URI,
    NS,
)
from psir_enrich.enrich import (
    enrich_one,
    enrich_one_expanded,
    run_enrichment,
    EnrichmentResult,
)
from psir_enrich.wos_client import WosStarterClient
from psir_enrich.wos_expanded_client import WosExpandedClient

__all__ = [
    "__version__",
    # State and XML core
    "ArticleState",
    "survey_article",
    "parse_csl_wos",
    "parse_csl_pmid",
    "parse_existing_extids",
    "norm_doi",
    "norm_wos_ut",
    "norm_pmid",
    "is_meeting_abstract",
    "build_full_output_xml",
    "inject_expanded_metadata",
    "EXTID_DEFINITIONS",
    "NS_URI",
    "NS",
    # Pipeline
    "enrich_one",
    "enrich_one_expanded",
    "run_enrichment",
    "EnrichmentResult",
    # API clients
    "WosStarterClient",
    "WosExpandedClient",
]
