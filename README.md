# psir-enrich

**Enrich OMEGA-PSIR publication XML records with Web of Science identifiers, PubMed IDs, Scopus IDs, and selected WoS Expanded metadata.**

`psir-enrich` provides a Streamlit GUI for routine use and a CLI for batch work. It was built for the OMEGA-PSIR 4.6.4 installation at the Medical University of Varna, with MU-Varna dictionary UUIDs.

---

## Current capabilities

For each `<ns2:article>` in a PSIR XML export, the tool builds an audit trail and, when needed, writes an output `<collection>` containing only the enriched records.

### Identifier enrichment

1. Reads direct article-level `<extid>` blocks for `WoSId`, `ScopusId`, and `PubMedID`.
2. Promotes publication WoS IDs already present in CSL JSON:
   - `id`: `WOS:...`, `CABI:...`, `MEDLINE:...`, etc.
   - `note` / `annote`: text such as `Web of Science ID: WOS:...` or `Web of Science ID: CABI:...`.
3. Promotes Scopus IDs already present in CSL JSON:
   - `URL`: scopus.com record links (`2-s2.0-...` or `/publications/...`)
   - `id` / `note` / `annote`: explicitly labelled Scopus IDs
4. Searches DOI through Clarivate Starter API:
   - first `db=WOS`
   - then `db=MEDLINE`, when available through the plan/API response.
5. Searches DOI through Scopus Search API:
   - Returns Scopus document ID, PubMed ID, and article number in one call.
   - Falls back to title-based search when DOI is absent.
6. Falls back to NCBI PubMed ESearch when Clarivate does not resolve the DOI but PubMed does. In that case it stores:
   - `PubMedID = <PMID>`
   - `WoSId = MEDLINE:<PMID>`
7. For records with a known supported WoS UT but missing PMID, tries Starter UID lookup.
8. Skips PubMed lookup for meeting abstracts by default, but still stores available WoS identifiers.

### Expanded metadata enrichment

When a WoS Expanded API key is provided, records with an eligible UT are fetched through Expanded `/id/{uid}?optionView=FR`.

The tool fills these fields only when missing in PSIR (or when the Expanded API provides a value and the field is absent):

- `abstractEN`
- `keywordsEN`
- `collation` (page range derived from WoS `page.begin` / `page.end`)
- `articleNo` (written when the WoS Expanded record provides an article number and the source does not already have one)
- journal issue `vol`
- journal issue `no`

For page locations, WoS `page.begin` / `page.end` are mapped to PSIR `<collation>`. If the record has no pages, the tool will use the WoS article number (when present) as `<articleNo>`. Note: article numbers returned by Expanded are now written into the output whenever the Expanded API supplies one (subject to not overwriting an existing `articleNo` in the source XML). **`articleNo` and `collation` can coexist in the output.**

For issue number, WoS `issue` is preferred. If there is no regular issue, supplement/special issue values are mapped to PSIR `<no>`, for example:

- `supplement = 1` → `Suppl 1`
- `supplement = 1`, `special_issue = SI` → `Suppl 1, SI`

The tool writes or updates these WoS-derived userfields:

- `wos_categories`
- `wos_research_areas`
- `wos_keywords_plus`
- `wos_grant_agencies`
- `wos_grant_ids`
- `wos_fund_text`

`wos_fund_text` is always written or overwritten from WoS funding text when available. The generic PSIR `Funding` userfield is left untouched.

### Scopus metadata enrichment

When a Scopus API key is provided:

- Fills missing `ScopusId` (numeric format) via the Scopus Search API.
- If `abstractEN` or `keywordsEN` are missing, fetches them via the Scopus Abstract Retrieval API (`view=FULL`).
- Extracts `articleNo` from `prism:startingPage` / `prism:endingPage` / `prism:articleNumber` or the bibrecord `pagerange`.
- Extracts `PubMedID` when available in the Scopus record.

### Output files

Each run produces:

- enriched XML containing only changed records
- audit CSV containing all input records and the action/skip reason for each one

---

## Quick GUI use

1. Upload a PSIR XML export.
2. Paste a Clarivate Starter API key, or leave it blank for CSL-only enrichment.
3. Optionally paste a WoS Expanded API key for metadata enrichment.
4. Optionally paste an Elsevier Scopus API key for Scopus enrichment.
5. Click **Run enrichment**.
6. Download the enriched XML and audit CSV.

---

## Run locally

```bash
git clone https://github.com/lowhumlee/psir-enrich.git
cd psir-enrich
python -m pip install -e ".[gui]"
streamlit run app.py
