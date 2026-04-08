---
description: Run and verify closed-loop development cycle
---

## Overview

This workflow ensures all changes follow the Ralph Loop closed-loop verification pattern.

## Prerequisites
- Backend running: `cd backend && python -m uvicorn app.main:app --reload --port 8000`
- Frontend running: `cd frontend/test-studio && npm run dev`

## Steps

// turbo
1. Run backend tests
   ```bash
   cd backend && pytest --tb=short -q
   ```

// turbo
2. Run frontend type check
   ```bash
   cd frontend/test-studio && npm run build
   ```

// turbo
3. Run frontend lint
   ```bash
   cd frontend/test-studio && npm run lint
   ```

4. Run E2E tests (if modifying critical paths)
   ```bash
   npx playwright test --reporter=list
   ```

## Verification

- [ ] All backend tests pass
- [ ] Frontend builds without TypeScript errors
- [ ] No lint warnings
- [ ] E2E tests pass (if applicable)

## When to Run

Run this workflow:
- Before every commit
- After completing a feature
- Before creating a pull request
- After resolving merge conflicts

## Troubleshooting

### Tests fail with import errors
**Symptom**: `ModuleNotFoundError` in Python tests
**Fix**: Ensure virtual environment is activated and dependencies installed:
```bash
cd backend
pip install -r requirements.txt
```

### TypeScript build fails
**Symptom**: Type errors during `npm run build`
**Fix**: Check the error message, fix type issues. Common causes:
- Missing type definitions
- Incorrect prop types
- Async function return types
