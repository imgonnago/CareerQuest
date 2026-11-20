"""FastAPI endpoint contract tests without sending HTTP requests or writing to DB."""
from __future__ import annotations

import importlib
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from src.gap_service import GapInputError, GapRequest, GapResultItem


class BackendApiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = importlib.import_module("main")

    def test_gap_path_in_openapi(self):
        doc = self.api.app.openapi()
        self.assertIn("post", doc["paths"]["/api/gap/calculate"])

    def test_model_and_gap_contract_endpoints_without_db(self):
        self.assertEqual(self.api.model_contract()["contract_version"], "student_experience_skill_v1")
        self.assertIn("input_example", self.api.gap_contract())

    def test_valid_gap_endpoint_with_stubbed_business_logic(self):
        req = GapRequest(student_id=1, skills=[{"skill_id": 1, "skill": "Python"}])
        response = [{"skill_id": 1, "skill": "Python", "student_level": None, "gap": "미보유"}]
        with patch.object(self.api, "calculate_gap_request", return_value=response) as calc:
            self.assertEqual(self.api.calculate_gap(req), response)
            calc.assert_called_once_with(req)

    def test_unmapped_response_contract(self):
        item = GapResultItem(skill_id=None, skill="Not in dictionary", student_level=None, gap="매핑되지 않음")
        self.assertEqual(item.model_dump()["gap"], "매핑되지 않음")

    def test_duplicate_skill_id_rejected_before_db(self):
        with self.assertRaises(ValidationError):
            GapRequest(student_id=1, skills=[{"skill_id": 1}, {"skill_id": 1}])

    def test_unmapped_missing_name_rejected_before_db(self):
        with self.assertRaises(ValidationError):
            GapRequest(student_id=1, skills=[{"skill_id": None}])

    def test_level_order_required_for_level_comparison(self):
        with self.assertRaises(ValidationError):
            GapRequest(student_id=1, skills=[{"skill_id": 1, "required_level": "IM"}])

    def test_invalid_student_id_rejected(self):
        with self.assertRaises(ValidationError):
            GapRequest(student_id=0, skills=[])

    def test_gap_business_validation_maps_to_http422(self):
        req = GapRequest(student_id=1, skills=[{"skill_id": 1}])
        with patch.object(self.api, "calculate_gap_request", side_effect=GapInputError("invalid skill id")):
            with self.assertRaises(HTTPException) as captured:
                self.api.calculate_gap(req)
        self.assertEqual(captured.exception.status_code, 422)

    def test_gap_database_error_is_500_without_internal_leak(self):
        req = GapRequest(student_id=1, skills=[{"skill_id": 1}])
        secret = "internal-password-sentinel"
        with patch.object(self.api, "calculate_gap_request", side_effect=SQLAlchemyError(secret)):
            with self.assertRaises(HTTPException) as captured:
                self.api.calculate_gap(req)
        self.assertEqual(captured.exception.status_code, 500)
        self.assertNotIn(secret, str(captured.exception.detail))

    def test_fetch_all_db_error_has_no_internal_details(self):
        secret = "internal-password-sentinel"
        with patch.object(self.api.engine, "connect", side_effect=SQLAlchemyError(secret)):
            with self.assertRaises(HTTPException) as captured:
                self.api.fetch_all("SELECT 1")
        self.assertEqual(captured.exception.status_code, 500)
        self.assertNotIn(secret, str(captured.exception.detail))

    def test_fetch_scalar_db_error_has_no_internal_details(self):
        secret = "internal-password-sentinel"
        with patch.object(self.api.engine, "connect", side_effect=SQLAlchemyError(secret)):
            with self.assertRaises(HTTPException) as captured:
                self.api.fetch_scalar("SELECT 1")
        self.assertEqual(captured.exception.status_code, 500)
        self.assertNotIn(secret, str(captured.exception.detail))

    def test_no_unauthenticated_proposal_management_api_added(self):
        # New approval flow remains CLI-only until auth/roles are designed.
        for route in self.api.app.routes:
            if hasattr(route, "methods") and route.path.startswith("/api/proposals"):
                self.fail("Unexpected proposal route added; audit authorization first")


if __name__ == "__main__":
    unittest.main()
