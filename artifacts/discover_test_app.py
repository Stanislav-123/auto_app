#!/usr/bin/env python3
"""
Read-only AppsFlyer app discovery for integration testing.

Inspects Unity/TikTok/Snapchat integration status without mutations.
Reports first app with all three partners unconfigured.

Usage:
    python discover_test_app.py --base-path /path/to/production
    python discover_test_app.py --base-path /path/to/production --app io.example.app
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def load_env(base_path: Path) -> None:
    """Load .env from base path."""
    env_file = base_path / ".env"
    if not env_file.exists():
        print(f"ERROR: .env not found at {env_file}")
        sys.exit(1)
    
    try:
        from dotenv import load_dotenv
        load_dotenv(env_file)
        print(f"Loaded .env from {env_file}")
    except ImportError:
        print("ERROR: python-dotenv not installed")
        sys.exit(1)


def setup_imports(base_path: Path) -> None:
    """Add base path to sys.path to import production code."""
    if str(base_path) not in sys.path:
        sys.path.insert(0, str(base_path))


def discover_apps(session: Any) -> list[dict[str, str]]:
    """Discover apps available in AppsFlyer UI (read-only)."""
    page = session.page
    if page is None:
        return []
    
    # Navigate to apps overview
    try:
        page.goto("https://hq1.appsflyer.com/apps/myapps", wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception as e:
        print(f"WARNING: Could not load apps page: {e}")
        return []
    
    # Look for app cards/rows in the UI
    apps = []
    try:
        # Try common selectors for app lists
        app_elements = page.locator('[data-qa-id*="app"], [class*="app-card"], [class*="app-row"]')
        count = min(app_elements.count(), 20)  # Bounded to first 20
        
        for index in range(count):
            elem = app_elements.nth(index)
            # Extract app name and bundle/store id if visible
            text = elem.inner_text() if elem.count() else ""
            if text and len(text) < 500:
                # Simple heuristic: look for bundle-like patterns
                parts = text.split()
                for part in parts:
                    if "." in part and len(part) > 5:
                        apps.append({"name": text[:100], "bundle": part})
                        break
        
        if not apps:
            print("WARNING: Could not parse app list from UI")
    except Exception as e:
        print(f"WARNING: Error discovering apps: {e}")
    
    return apps


def inspect_partner(session: Any, app: str, partner_id: str) -> dict[str, Any]:
    """Inspect partner integration status (read-only, no saves)."""
    try:
        url = f"https://hq1.appsflyer.com/marketplace/integrated-partners/{app}/{partner_id}"
        session._goto(url)
        session._wait_content()
        
        # Check if integrated
        page = session.page
        integrated = session._looks_like_config()
        
        if not integrated:
            return {"integrated": False, "active": False, "game_id": "", "tags": [], "events": []}
        
        # Read integration state using existing methods
        if partner_id == "unityads_int":
            page_data = session._read_unity_settled()
        else:
            page_data = session._read_integration()
        
        return {
            "integrated": page_data.integrated,
            "active": page_data.active,
            "game_id": page_data.game_id[:20] if hasattr(page_data, "game_id") else "",  # Truncate
            "tags": [t[:20] for t in (page_data.tags[:3] if hasattr(page_data, "tags") else [])],  # First 3, truncated
            "events": [
                {"af": e.af_event, "partner": e.partner_event[:20]}
                for e in (page_data.rows[:5] if hasattr(page_data, "rows") else [])  # First 5
            ]
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover AppsFlyer test app (read-only)")
    parser.add_argument("--base-path", required=True, help="Path to production repo with .env")
    parser.add_argument("--app", help="Specific app to inspect (skip discovery)")
    parser.add_argument("--output", default="artifacts/discovery_result.json", help="Output JSON file")
    args = parser.parse_args()
    
    base_path = Path(args.base_path).resolve()
    if not base_path.is_dir():
        print(f"ERROR: Base path not found: {base_path}")
        return 1
    
    # Load environment and imports
    load_env(base_path)
    setup_imports(base_path)
    
    # Import after setting up path
    try:
        from src.appsflyer.playwright_session import PlaywrightAppsFlyer
    except ImportError as e:
        print(f"ERROR: Could not import PlaywrightAppsFlyer: {e}")
        return 1
    
    # Ensure headed mode for read-only inspection
    os.environ["AF_HEADLESS"] = "0"
    
    result = {
        "success": False,
        "candidate_app": None,
        "unity_status": None,
        "tiktok_status": None,
        "snapchat_status": None,
        "error": None,
    }
    
    try:
        with PlaywrightAppsFlyer.from_env() as session:
            session.ensure_session()
            
            # Discover or use provided app
            if args.app:
                candidate = args.app
                print(f"Inspecting specified app: {candidate}")
            else:
                print("Discovering apps...")
                apps = discover_apps(session)
                if not apps:
                    result["error"] = "No apps discovered - provide explicit --app argument"
                    print(f"ERROR: {result['error']}")
                    return 1
                print(f"Found {len(apps)} apps")
                candidate = apps[0]["bundle"]  # Use first for now
            
            result["candidate_app"] = candidate
            
            # Inspect Unity
            print(f"Inspecting Unity for {candidate}...")
            unity = inspect_partner(session, candidate, "unityads_int")
            result["unity_status"] = unity
            
            # Inspect TikTok
            print(f"Inspecting TikTok for {candidate}...")
            tiktok = inspect_partner(session, candidate, "tiktokglobal_int")
            result["tiktok_status"] = tiktok
            
            # Inspect Snapchat
            print(f"Inspecting Snapchat for {candidate}...")
            snapchat = inspect_partner(session, candidate, "snapchat_int")
            result["snapchat_status"] = snapchat
            
            # Check if all three are unconfigured or have minimal config
            all_unconfigured = (
                not unity.get("integrated") and
                not tiktok.get("integrated") and
                not snapchat.get("integrated")
            )
            
            result["success"] = True
            result["all_unconfigured"] = all_unconfigured
            
            print(f"\n{'='*60}")
            print(f"CANDIDATE APP: {candidate}")
            print(f"{'='*60}")
            print(f"Unity: {'Not integrated' if not unity.get('integrated') else f'Integrated (active={unity.get('active')})'}")
            print(f"TikTok: {'Not integrated' if not tiktok.get('integrated') else f'Integrated (active={tiktok.get('active')})'}")
            print(f"Snapchat: {'Not integrated' if not snapchat.get('integrated') else f'Integrated (active={snapchat.get('active')})'}")
            print(f"\nAll unconfigured: {all_unconfigured}")
            print(f"{'='*60}\n")
            
    except Exception as e:
        result["error"] = str(e)[:500]
        print(f"ERROR: {result['error']}")
        return 1
    
    # Write sanitized result
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResult written to {output_path}")
    
    if result.get("all_unconfigured"):
        print(f"\n✓ Found eligible test app: {candidate}")
        print("  All three partners (Unity/TikTok/Snapchat) are unconfigured.")
        print("\nParent can now announce this app and provide test IDs for live integration test.")
    else:
        print(f"\n✗ App {candidate} has existing integrations.")
        print("  Parent should choose different app or verify current state is acceptable for testing.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
