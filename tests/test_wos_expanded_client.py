from psir_enrich.wos_expanded_client import WosExpandedClient


def test_extract_funding_handles_none_fund_ack():
    rec = {
        "static_data": {
            "fullrecord_metadata": {
                "fund_ack": None
            }
        }
    }

    result = WosExpandedClient.extract_funding(rec)

    assert result == {
        "fund_text": None,
        "agencies": [],
        "grant_ids": [],
    }


def test_extract_funding_handles_missing_fund_ack():
    rec = {
        "static_data": {
            "fullrecord_metadata": {}
        }
    }

    result = WosExpandedClient.extract_funding(rec)

    assert result == {
        "fund_text": None,
        "agencies": [],
        "grant_ids": [],
    }


def test_extract_funding_extracts_valid_structured_funding():
    rec = {
        "static_data": {
            "fullrecord_metadata": {
                "fund_ack": {
                    "fund_text": {
                        "p": "Supported by Example Fund."
                    },
                    "grants": {
                        "grant": {
                            "grant_agency": "Example Agency",
                            "grant_agency_names": [
                                {
                                    "pref": "Y",
                                    "content": "Example Preferred Agency"
                                }
                            ],
                            "grant_ids": {
                                "grant_id": ["A123", "B456"]
                            }
                        }
                    }
                }
            }
        }
    }

    result = WosExpandedClient.extract_funding(rec)

    assert result["fund_text"] == "Supported by Example Fund."
    assert result["agencies"] == [
        "Example Agency",
        "Example Preferred Agency",
    ]
    assert result["grant_ids"] == ["A123", "B456"]


def test_extract_pub_info_uses_supplement_as_issue_fallback():
    rec = {
        "static_data": {
            "summary": {
                "pub_info": {
                    "vol": "18",
                    "supplement": "1",
                    "page": {
                        "begin": "34",
                        "end": "34"
                    }
                }
            }
        }
    }

    result = WosExpandedClient.extract_pub_info(rec)

    assert result["vol"] == "18"
    assert result["issue"] == "Suppl 1"
    assert result["supplement"] == "Suppl 1"
    assert result["page_begin"] == "34"
    assert result["page_end"] == "34"


def test_extract_pub_info_prefers_issue_over_supplement():
    rec = {
        "static_data": {
            "summary": {
                "pub_info": {
                    "vol": "18",
                    "issue": "2",
                    "supplement": "1"
                }
            }
        }
    }

    result = WosExpandedClient.extract_pub_info(rec)

    assert result["vol"] == "18"
    assert result["issue"] == "2"
    assert result["supplement"] == "Suppl 1"
    
def test_extract_pub_info_combines_supplement_and_special_issue():
    rec = {
        "static_data": {
            "summary": {
                "pub_info": {
                    "vol": "18",
                    "supplement": "1",
                    "special_issue": "SI"
                }
            }
        }
    }

    result = WosExpandedClient.extract_pub_info(rec)

    assert result["vol"] == "18"
    assert result["issue"] == "Suppl 1, SI"
    assert result["supplement"] == "Suppl 1"
    assert result["special_issue"] == "SI"
