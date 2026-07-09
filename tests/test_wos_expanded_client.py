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
