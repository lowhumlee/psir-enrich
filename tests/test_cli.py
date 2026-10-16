"""Tests for psir_enrich.cli — argument parsing and pipeline invocation."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from psir_enrich.cli import _build_parser, main


class TestBuildParser:
    def test_required_args(self):
        parser = _build_parser()
        args = parser.parse_args(["-i", "in.xml", "-o", "out.xml"])
        assert args.input == Path("in.xml")
        assert args.output == Path("out.xml")

    def test_no_api_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["-i", "in.xml", "-o", "out.xml", "--no-api"])
        assert args.no_api is True

    def test_api_key(self):
        parser = _build_parser()
        args = parser.parse_args([
            "-i", "in.xml", "-o", "out.xml", "--api-key", "test123"
        ])
        assert args.api_key == "test123"

    def test_expanded_api_key(self):
        parser = _build_parser()
        args = parser.parse_args([
            "-i", "in.xml", "-o", "out.xml", "--expanded-api-key", "exp456"
        ])
        assert args.expanded_api_key == "exp456"

    def test_scopus_api_key(self):
        parser = _build_parser()
        args = parser.parse_args([
            "-i", "in.xml", "-o", "out.xml", "--scopus-api-key", "scopus789"
        ])
        assert args.scopus_api_key == "scopus789"

    def test_plan_default(self):
        parser = _build_parser()
        args = parser.parse_args(["-i", "in.xml", "-o", "out.xml"])
        assert args.plan == "subscriber"

    def test_plan_choices(self):
        parser = _build_parser()
        for plan in ("free", "subscriber", "advanced"):
            args = parser.parse_args(["-i", "in.xml", "-o", "out.xml", "--plan", plan])
            assert args.plan == plan

    def test_rate_limit_override(self):
        parser = _build_parser()
        args = parser.parse_args([
            "-i", "in.xml", "-o", "out.xml", "--rate-limit", "1.5"
        ])
        assert args.rate_limit == 1.5

    def test_include_meeting_abstracts(self):
        parser = _build_parser()
        args = parser.parse_args([
            "-i", "in.xml", "-o", "out.xml", "--include-meeting-abstracts-pmid"
        ])
        assert args.include_meeting_abstracts_pmid is True


class TestMainFunction:
    def test_missing_input_file(self, tmp_path):
        """Should return exit code 2 when input file doesn't exist."""
        result = main([
            "-i", str(tmp_path / "nonexistent.xml"),
            "-o", str(tmp_path / "out.xml"),
            "--no-api",
        ])
        assert result == 2

    @patch("psir_enrich.cli.run_enrichment")
    def test_no_api_mode(self, mock_run, tmp_path):
        """In --no-api mode, api_key should be None."""
        input_file = tmp_path / "test.xml"
        input_file.write_text("<collection></collection>")
        output_file = tmp_path / "out.xml"

        mock_result = MagicMock()
        mock_result.n_articles = 0
        mock_result.n_enriched = 0
        mock_result.n_api_calls = 0
        mock_result.n_api_errors = 0
        mock_result.n_expanded_calls = 0
        mock_result.n_expanded_errors = 0
        mock_result.n_scopus_calls = 0
        mock_result.n_scopus_errors = 0
        mock_result.output_xml_bytes = b"<collection/>"
        mock_result.audit_df = MagicMock()
        mock_run.return_value = mock_result

        result = main([
            "-i", str(input_file),
            "-o", str(output_file),
            "--no-api",
        ])

        assert result == 0
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["api_key"] is None
        assert call_kwargs["expanded_api_key"] is None
        assert call_kwargs["scopus_api_key"] is None

    @patch("psir_enrich.cli.run_enrichment")
    @patch.dict("os.environ", {"WOS_API_KEY": "env_key", "SCOPUS_API_KEY": "env_scopus"})
    def test_env_var_keys(self, mock_run, tmp_path):
        """Keys should be read from environment variables."""
        input_file = tmp_path / "test.xml"
        input_file.write_text("<collection></collection>")
        output_file = tmp_path / "out.xml"

        mock_result = MagicMock()
        mock_result.n_articles = 0
        mock_result.n_enriched = 0
        mock_result.n_api_calls = 0
        mock_result.n_api_errors = 0
        mock_result.n_expanded_calls = 0
        mock_result.n_expanded_errors = 0
        mock_result.n_scopus_calls = 0
        mock_result.n_scopus_errors = 0
        mock_result.output_xml_bytes = b"<collection/>"
        mock_result.audit_df = MagicMock()
        mock_run.return_value = mock_result

        result = main([
            "-i", str(input_file),
            "-o", str(output_file),
        ])

        assert result == 0
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["api_key"] == "env_key"
        assert call_kwargs["scopus_api_key"] == "env_scopus"
