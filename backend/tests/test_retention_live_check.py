import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.retention_live_check import run_live_check  # noqa: E402


TEST_EMAIL = "homen@retention.com"


def test_live_retention_install_path() -> None:
    result = run_live_check(email=TEST_EMAIL, platform="claude-code")

    assert result["ok"], json.dumps(result, indent=2)

    checks = {item["name"]: item for item in result["checks"]}

    assert checks["installer"]["status"] == 200
    assert "text/plain" in checks["installer"]["content_type"].lower()
    assert checks["installer"]["first_line"] == "#!/usr/bin/env bash"

    assert checks["token"]["ok"] is True
    assert checks["token"]["token_length"] > 0

    assert checks["proxy"]["status"] == 200
    assert checks["proxy"]["first_line"] == "#!/usr/bin/env python3"

    assert checks["clean_room"]["returncode"] == 0
    assert checks["clean_room"]["config_exists"] is True
    assert checks["clean_room"]["proxy_exists"] is True
    assert checks["clean_room"]["retention_url"] == "https://retention-backend.onrender.com"

    print("\nVOICE_MEMO_START")
    print(result["voice_memo"])
    print("VOICE_MEMO_END")
