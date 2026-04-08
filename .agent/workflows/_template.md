---
description: Template for creating new workflow documentation
---

# Workflow Template

Use this template when creating new workflow files in `.agent/workflows/`.

## File Naming Convention

- Use lowercase with hyphens: `deploy-staging.md`, `run-golden-bugs.md`
- Name should describe the action: `{verb}-{noun}.md`

## Template Structure

```markdown
---
description: Short one-line description of what this workflow does
---

## Prerequisites
- [ ] Prerequisite 1
- [ ] Prerequisite 2

## Steps

1. First step description
   ```bash
   command-to-run
   ```

// turbo
2. Safe step that can auto-run
   ```bash
   safe-command
   ```

3. Step requiring human judgment
   ```bash
   potentially-destructive-command
   ```

## Verification
- [ ] Verify check 1
- [ ] Verify check 2

## Troubleshooting

### Common Issue 1
**Symptom**: Description
**Fix**: Solution

### Common Issue 2
**Symptom**: Description
**Fix**: Solution
```

## Annotations

| Annotation | Meaning | When to Use |
|------------|---------|-------------|
| `// turbo` | Auto-run this single step | Safe, non-destructive commands |
| `// turbo-all` | Auto-run ALL steps | Completely safe workflows |

## Examples of Workflows to Document

- Deploying to staging/production
- Running benchmarks
- Setting up new developer environment
- Running specific test suites
- Database migrations
- Generating API documentation
