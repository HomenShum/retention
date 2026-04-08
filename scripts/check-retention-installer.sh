#!/usr/bin/env bash

set -euo pipefail

URL="${1:-${RETENTION_INSTALLER_URL:-https://retention.sh/install.sh}}"
EXPECTED_PREFIX='#!/usr/bin/env bash'
MAX_ATTEMPTS="${MAX_ATTEMPTS:-45}"
SLEEP_SECONDS="${SLEEP_SECONDS:-10}"

TMP_DIR="$(mktemp -d)"
HEADER_FILE="$TMP_DIR/headers.txt"
BODY_FILE="$TMP_DIR/body.txt"

cleanup() {
  rm -rf "$TMP_DIR"
}

trap cleanup EXIT

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  : > "$HEADER_FILE"
  : > "$BODY_FILE"

  status="$(curl -sS -L -D "$HEADER_FILE" -o "$BODY_FILE" -w "%{http_code}" "$URL" || true)"
  content_type="$(grep -i '^content-type:' "$HEADER_FILE" | tail -1 | tr -d '\r' | cut -d: -f2- | xargs || true)"
  first_line="$(head -n 1 "$BODY_FILE" 2>/dev/null || true)"

  if [ "$status" = "200" ] \
    && [[ "$content_type" == *"text/plain"* ]] \
    && [ "$first_line" = "$EXPECTED_PREFIX" ]; then
    echo "PASS: installer is live at $URL"
    echo "status=$status"
    echo "content_type=$content_type"
    echo "first_line=$first_line"
    exit 0
  fi

  echo "Attempt $attempt/$MAX_ATTEMPTS failed for $URL"
  echo "status=${status:-missing}"
  echo "content_type=${content_type:-missing}"
  echo "first_line=${first_line:-missing}"

  if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
    sleep "$SLEEP_SECONDS"
  fi

  attempt=$((attempt + 1))
done

echo "FAIL: installer regression detected for $URL" >&2
echo "Expected: status=200, content-type contains text/plain, first line is $EXPECTED_PREFIX" >&2
exit 1