---
description: Run golden bug evaluation tests
---

## Overview

Execute deterministic golden bug test cases for regression testing. Golden bugs bypass multi-agent orchestration for fast, reproducible results.

## Prerequisites
- [ ] Backend running on port 8000
- [ ] At least one Android emulator running (`adb devices`)
- [ ] `backend/data/golden_bugs.json` has valid golden bugs

## Steps

// turbo
1. Check emulator status
   ```bash
   adb devices -l
   ```

// turbo
2. Verify backend is running
   ```bash
   curl -s http://localhost:8000/health | jq .
   ```

3. List available golden bugs via API
   ```bash
   curl -s http://localhost:8000/api/golden-bugs | jq .
   ```

4. Run a specific golden bug
   ```bash
   curl -X POST http://localhost:8000/api/golden-bugs/GOLDEN-001/run \
     -H "Content-Type: application/json" \
     -d '{"device_id": "emulator-5554"}' | jq .
   ```

5. Or run via AI chat
   - Open http://localhost:5173
   - Click chat button (bottom right)
   - Type: "Run golden bug GOLDEN-001"

## Verification

- [ ] Golden bug execution completes
- [ ] Status shows PASSED or expected classification (TP/FP/TN/FN)
- [ ] Screenshot captured and saved
- [ ] Result appears in Agent Sessions (`/agent_sessions`)

## Troubleshooting

### "No golden bugs found"
**Symptom**: List returns empty array
**Fix**: Check `backend/data/golden_bugs.json` has valid entries

### "Device not found"
**Symptom**: Golden bug fails with device error
**Fix**: 
```bash
adb devices  # Verify emulator is running
adb -s emulator-5554 shell getprop ro.product.model  # Test connectivity
```

### "FAILED" status
**Symptom**: Golden bug returns FAILED classification
**Fix**: This may be expected behavior if testing for bug reproduction. Check:
- `expected_outcome` in golden bug config
- `auto_check` conditions
