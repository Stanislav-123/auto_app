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


def discover_apps(session: Any, max_apps: int = 20) -> list[dict[str, str]]:
    """Discover apps available in AppsFlyer UI (read-only, bounded)."""
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
        # Try to find app links with bundle/store IDs
        # AppsFlyer lists apps with their bundle IDs visible
        links = page.locator('a[href*="/apps/"]')
        count = min(links.count(), max_apps)
        
        for index in range(count):
            link = links.nth(index)
            href = link.get_attribute("href") or ""
            text = link.inner_text() if link.count() else ""
            
            # Extract bundle/store ID from href or text
            if "/apps/" in href:
                parts = href.split("/apps/")
                if len(parts) > 1:
                    bundle = parts[1].split("/")[0].strip()
                    if bundle and len(bundle) > 3:
                        apps.append({
                            "bundle": bundle,
                            "name": text[:80].strip() if text else bundle
                        })
        
        # Fallback: parse text content
        if not apps:
            app_elements = page.locator('[data-qa-id*="app"], [class*="app-card"], [class*="app-row"], td, li')
            elem_count = min(app_elements.count(), max_apps * 2)
            
            for index in range(elem_count):
                elem = app_elements.nth(index)
                text = elem.inner_text() if elem.count() else ""
                if text and len(text) < 500:
                    # Look for bundle-like patterns: com.example.app or id12345678
                    parts = text.split()
                    for part in parts:
                        part_clean = part.strip('(),[]{}')
                        # Bundle ID or store ID pattern
                        if (("." in part_clean and len(part_clean) > 5) or 
                            (part_clean.startswith("id") and part_clean[2:].isdigit() and len(part_clean) > 8)):
                            apps.append({"bundle": part_clean, "name": text[:80].strip()})
                            break
                if len(apps) >= max_apps:
                    break
        
        # Remove duplicates
        seen = set()
        unique_apps = []
        for app in apps:
            if app["bundle"] not in seen:
                seen.add(app["bundle"])
                unique_apps.append(app)
        
        apps = unique_apps[:max_apps]
        
        if not apps:
            print("WARNING: Could not parse app list from UI")
            print(f"Page URL: {page.url}")
            # Take screenshot for debugging
            screenshot_path = Path("artifacts/discovery_no_apps.png")
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(screenshot_path))
                print(f"Screenshot saved to {screenshot_path}")
            except Exception:
                pass
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
    
    # Use private temporary state file (read-only, don't save to production)
    import tempfile
    temp_state_fd, temp_state_path = tempfile.mkstemp(suffix=".json", prefix="af_discover_")
    os.close(temp_state_fd)
    temp_state_file = Path(temp_state_path)
    
    # Copy production state if exists
    prod_state = base_path / "data" / "af_state.json"
    if prod_state.exists():
        import shutil
        shutil.copy(prod_state, temp_state_file)
        print(f"Copied state from {prod_state}")
    
    # Override state path to use temp (won't save to production)
    os.environ["AF_STATE_PATH"] = str(temp_state_file)
    
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
                candidates = [args.app]
                print(f"Inspecting specified app: {args.app}")
            else:
                print("Discovering apps...")
                apps = discover_apps(session, max_apps=20)
                if not apps:
                    result["error"] = "No apps discovered - provide explicit --app argument"
                    print(f"ERROR: {result['error']}")
                    return 1
                print(f"Found {len(apps)} apps: {', '.join(a['bundle'] for a in apps[:5])}{'...' if len(apps) > 5 else ''}")
                candidates = [a["bundle"] for a in apps]
            
            result["apps_checked"] = []
            
            # Check all candidates for first with all 3 unconfigured
            for idx, candidate in enumerate(candidates):
                print(f"\n[{idx+1}/{len(candidates)}] Checking {candidate}...")
                
                app_result = {
                    "app": candidate,
                    "unity": None,
                    "tiktok": None,
                    "snapchat": None,
                    "all_unconfigured": False
                }
                
                try:
                    # Inspect all three partners
                    unity = inspect_partner(session, candidate, "unityads_int")
                    tiktok = inspect_partner(session, candidate, "tiktokglobal_int")
                    snapchat = inspect_partner(session, candidate, "snapchat_int")
                    
                    app_result["unity"] = unity
                    app_result["tiktok"] = tiktok
                    app_result["snapchat"] = snapchat
                    
                    # Check if all unconfigured
                    all_unconfigured = (
                        not unity.get("integrated") and
                        not tiktok.get("integrated") and
                        not snapchat.get("integrated")
                    )
                    app_result["all_unconfigured"] = all_unconfigured
                    
                    result["apps_checked"].append(app_result)
                    
                    if all_unconfigured and not result.get("candidate_app"):
                        result["candidate_app"] = candidate
                        result["unity_status"] = unity
                        result["tiktok_status"] = tiktok
                        result["snapchat_status"] = snapchat
                        print(f"  ✓ Found eligible app with all 3 unconfigured!")
                    else:
                        unity_str = "✗" if unity.get("integrated") else "✓"
                        tiktok_str = "✗" if tiktok.get("integrated") else "✓"
                        snap_str = "✗" if snapchat.get("integrated") else "✓"
                        print(f"  Unity:{unity_str} TikTok:{tiktok_str} Snapchat:{snap_str}")
                        
                except Exception as e:
                    print(f"  ERROR inspecting {candidate}: {str(e)[:100]}")
                    app_result["error"] = str(e)[:200]
                    result["apps_checked"].append(app_result)
            
            if result.get("candidate_app"):
                candidate = result["candidate_app"]
                
                result["success"] = True
                
                print(f"\n{'='*60}")
                print(f"BEST CANDIDATE: {candidate}")
                print(f"{'='*60}")
                
                # Format status without nested f-strings
                unity = result["unity_status"]
                tiktok = result["tiktok_status"]
                snapchat = result["snapchat_status"]
                
                unity_status = "Not integrated" if not unity.get('integrated') else f"Integrated (active={unity.get('active')})"
                tiktok_status = "Not integrated" if not tiktok.get('integrated') else f"Integrated (active={tiktok.get('active')})"
                snapchat_status = "Not integrated" if not snapchat.get('integrated') else f"Integrated (active={snapchat.get('active')})"
                
                print(f"Unity: {unity_status}")
                print(f"TikTok: {tiktok_status}")
                print(f"Snapchat: {snapchat_status}")
                print(f"{'='*60}\n")
            else:
                result["success"] = True
                print(f"\n{'='*60}")
                print(f"NO ELIGIBLE APP FOUND")
                print(f"{'='*60}")
                print(f"Checked {len(result['apps_checked'])} apps - none have all 3 unconfigured")
                print(f"See discovery_result.json for details")
                print(f"{'='*60}\n")
            
    except Exception as e:
        result["error"] = str(e)[:500]
        print(f"ERROR: {result['error']}")
        return 1
    finally:
        # Cleanup temporary state file
        try:
            if temp_state_file.exists():
                temp_state_file.unlink()
                print(f"Cleaned up temp state file")
        except Exception:
            pass
    
    # Write sanitized result
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResult written to {output_path}")
    
    if result.get("candidate_app"):
        print(f"\n✓ Found eligible test app: {result['candidate_app']}")
        print("  All three partners (Unity/TikTok/Snapchat) are unconfigured.")
        print("\nParent can now announce this app and provide test IDs for live integration test.")
    else:
        print(f"\n✗ No app found with all 3 partners unconfigured.")
        print(f"  Checked {len(result.get('apps_checked', []))} apps.")
        print("  Parent should review discovery_result.json and choose app manually.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
