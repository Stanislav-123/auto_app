#!/usr/bin/env python3
"""
Live AppsFlyer integration test using execute_share API.

Requires parent approval and test IDs before execution.
Uses production .env but test source files.

Usage:
    # Set test parameters
    set TEST_APP=io.example.testapp
    set TEST_UNITY_GAME_ID=1234567
    set TEST_TIKTOK_APP_ID=7234567890123456789
    set TEST_SNAPCHAT_APP_ID=uuid-1234-5678
    
    # Run test
    python run_live_test.py --base-path C:\path\to\production\auto_app
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


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
    """Add test directory (current) to sys.path BEFORE base_path.
    
    This ensures test source files are imported instead of production.
    """
    test_dir = Path(__file__).parent.parent.resolve()
    
    # Remove base_path if already in sys.path (prevent production import)
    if str(base_path) in sys.path:
        sys.path.remove(str(base_path))
    
    # Add test directory FIRST
    if str(test_dir) not in sys.path:
        sys.path.insert(0, str(test_dir))
    
    print(f"Using test source from: {test_dir}")


def get_test_params() -> dict[str, str]:
    """Get test parameters from environment."""
    app = os.getenv("TEST_APP", "").strip()
    unity_game_id = os.getenv("TEST_UNITY_GAME_ID", "").strip()
    tiktok_app_id = os.getenv("TEST_TIKTOK_APP_ID", "").strip()
    snapchat_app_id = os.getenv("TEST_SNAPCHAT_APP_ID", "").strip()
    
    missing = []
    if not app:
        missing.append("TEST_APP")
    if not unity_game_id:
        missing.append("TEST_UNITY_GAME_ID")
    if not tiktok_app_id:
        missing.append("TEST_TIKTOK_APP_ID")
    if not snapchat_app_id:
        missing.append("TEST_SNAPCHAT_APP_ID")
    
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("\nSet them before running:")
        print(f"  set TEST_APP=io.example.testapp")
        print(f"  set TEST_UNITY_GAME_ID=1234567")
        print(f"  set TEST_TIKTOK_APP_ID=7234567890123456789")
        print(f"  set TEST_SNAPCHAT_APP_ID=uuid-1234-5678")
        sys.exit(1)
    
    return {
        "app": app,
        "unity_game_id": unity_game_id,
        "tiktok_app_id": tiktok_app_id,
        "snapchat_app_id": snapchat_app_id,
    }


def run_integration_test(params: dict[str, str]) -> int:
    """Run live integration test using execute_share API."""
    from src.appsflyer.session import execute_share, ShareTask, AfError, AfBlocked
    
    app = params["app"]
    
    print(f"\n{'='*60}")
    print(f"LIVE INTEGRATION TEST")
    print(f"{'='*60}")
    print(f"App: {app}")
    print(f"Unity Game ID: {params['unity_game_id']}")
    print(f"TikTok App ID: {params['tiktok_app_id'][:20]}...")
    print(f"Snapchat App ID: {params['snapchat_app_id'][:20]}...")
    print(f"{'='*60}\n")
    
    results = {}
    all_success = True
    
    # Test Unity
    print(f"[1/3] Configuring Unity...")
    try:
        task = ShareTask(
            kind="unity",
            pairs=[(app, params["unity_game_id"])]
        )
        result = execute_share(task)
        results["unity"] = result[0] if result else None
        
        if result and result[0].status in ("done", "already"):
            print(f"  ✓ Unity: {result[0].status} - {result[0].reason}")
        else:
            print(f"  ✗ Unity: {result[0].status if result else 'no result'}")
            all_success = False
    except (AfError, AfBlocked) as e:
        print(f"  ✗ Unity FAILED: {e}")
        results["unity"] = {"error": str(e)[:200]}
        all_success = False
    
    # Test TikTok
    print(f"[2/3] Configuring TikTok...")
    try:
        task = ShareTask(
            kind="tiktok",
            ids=[params["tiktok_app_id"]],
            apps=[app]
        )
        result = execute_share(task)
        results["tiktok"] = result[0] if result else None
        
        if result and result[0].status in ("done", "already"):
            print(f"  ✓ TikTok: {result[0].status} - {result[0].reason}")
        else:
            print(f"  ✗ TikTok: {result[0].status if result else 'no result'}")
            all_success = False
    except (AfError, AfBlocked) as e:
        print(f"  ✗ TikTok FAILED: {e}")
        results["tiktok"] = {"error": str(e)[:200]}
        all_success = False
    
    # Test Snapchat
    print(f"[3/3] Configuring Snapchat...")
    try:
        task = ShareTask(
            kind="snapchat",
            ids=[params["snapchat_app_id"]],
            apps=[app]
        )
        result = execute_share(task)
        results["snapchat"] = result[0] if result else None
        
        if result and result[0].status in ("done", "already"):
            print(f"  ✓ Snapchat: {result[0].status} - {result[0].reason}")
        else:
            print(f"  ✗ Snapchat: {result[0].status if result else 'no result'}")
            all_success = False
    except (AfError, AfBlocked) as e:
        print(f"  ✗ Snapchat FAILED: {e}")
        results["snapchat"] = {"error": str(e)[:200]}
        all_success = False
    
    print(f"\n{'='*60}")
    if all_success:
        print("✓ ALL INTEGRATIONS SUCCESSFUL")
        print("\nVerify in AppsFlyer UI:")
        print("  1. Unity: Game ID set, events mapped (session/level_complete/purchase)")
        print("  2. TikTok: App ID set, events mapped (LaunchAPP/Registration/Purchase)")
        print("  3. Snapchat: App ID set, events mapped (APP_OPEN/SIGN_UP/PURCHASE)")
        print("  4. Events: af_app_opened, af_complete_registration, af_purchase exist")
        print("  5. All mappings: All media sources + Values & revenue")
        return 0
    else:
        print("✗ SOME INTEGRATIONS FAILED")
        print("\nCheck logs/ directory for screenshots and details")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run live AppsFlyer integration test")
    parser.add_argument("--base-path", required=True, help="Path to production repo with .env")
    args = parser.parse_args()
    
    base_path = Path(args.base_path).resolve()
    if not base_path.is_dir():
        print(f"ERROR: Base path not found: {base_path}")
        return 1
    
    # Load environment first (before imports)
    load_env(base_path)
    
    # Override state path to use production state
    state_file = base_path / "data" / "af_state.json"
    if state_file.exists():
        os.environ["AF_STATE_PATH"] = str(state_file)
        print(f"Using state file: {state_file}")
    
    # Force headed mode
    os.environ["AF_HEADLESS"] = "0"
    
    # Get test parameters
    params = get_test_params()
    
    # Setup imports (test source BEFORE production)
    setup_imports(base_path)
    
    # Run test
    try:
        return run_integration_test(params)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
