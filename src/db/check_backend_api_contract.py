"""CareerQuest read-only route/OpenAPI contract checker.

No HTTP server, DB queries, LLM, or SQL writes are used.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys

REQUIRED = {
    ("GET", "/health"),
    ("GET", "/api/skills"),
    ("GET", "/api/students"),
    ("GET", "/api/students/{student_id}/skills"),
    ("GET", "/api/model/contract"),
    ("GET", "/api/gap/contract"),
    ("POST", "/api/gap/calculate"),
}


def check() -> dict:
    mod = importlib.import_module("main")
    app = mod.app
    openapi = app.openapi()
    routes = {(method, route.path)
              for route in app.routes
              for method in (route.methods or set())
              if method not in {"HEAD", "OPTIONS"}}
    absent = sorted(REQUIRED - routes)
    assert not absent, f"Missing required routes: {absent}"

    gap_post = openapi["paths"]["/api/gap/calculate"]["post"]
    req = gap_post.get("requestBody", {})
    assert "application/json" in req.get("content", {}), "GAP JSON input missing"
    responses = gap_post.get("responses", {})
    assert "200" in responses and "422" in responses, "GAP success/validation responses missing"

    from src.gap_service import GapRequest, GapResultItem
    sample = GapRequest.model_validate({
        "student_id": 1,
        "skills": [
            {"skill_id": 1, "skill": "Python"},
            {"skill_id": None, "skill": "Unknown Skill"},
        ],
    })
    assert len(sample.skills) == 2
    assert GapResultItem(skill_id=None, skill="Unknown Skill", student_level=None,
                         gap="매핑되지 않음").gap == "매핑되지 않음"

    # A sensitive resource should never be assumed protected just because
    # the proposal lifecycle is CLI-only. Check and report, not alter contract.
    global_dependencies = getattr(app, "dependencies", []) or []
    student_route = next(r for r in app.routes if r.path == "/api/students")
    route_dependencies = getattr(getattr(student_route, "dependant", None), "dependencies", []) or []
    auth_warning = not (global_dependencies or route_dependencies)

    # The model-experience endpoint currently catches broad Exception and returns 404.
    # Static warning: actual error classification needs separate contract review.
    report = {
        "status": "PASS",
        "route_count": len(routes),
        "required_routes_checked": len(REQUIRED),
        "gap_json_200_422": True,
        "proposal_admin_routes_present": any("proposal" in p for _, p in routes),
        "security_warnings": [
            "GET /api/students has no apparent FastAPI dependency authorization; do not expose publicly without a separately verified auth layer"
        ] if auth_warning else [],
        "notes": [
            "This is a contract inspection, NOT a penetration test or production security certification",
            "GET student skills may expose raw StudentSkill; GAP service applies its separate trust policy",
            "Stored role_skill provenance is not verified by this tool",
        ],
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = check()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("[PASS] Required routes:", report["required_routes_checked"])
        print("[PASS] GAP JSON contract: 200 / 422")
        print("[INFO] Total registered methods+paths:", report["route_count"])
        for note in report["security_warnings"]:
            print("[SECURITY WARNING]", note)
        print("[READ ONLY] No HTTP server, model call, or DB writes")


if __name__ == "__main__":
    main()
