#!/usr/bin/env python3
"""
Script to upsert all bug reports from bugReports.json into the vector search index
"""
import json
import requests

# Load bug reports
with open('frontend/test-studio/src/data/bugReports.json', 'r') as f:
    data = json.load(f)

bug_reports = data['reports']

# Upsert each bug report
base_url = 'http://localhost:8000/api/search'
upserted_count = 0

for report in bug_reports:
    # Add description field (use title as description if not present)
    if 'description' not in report:
        report['description'] = report['title']
    
    # Add severity if not present
    if 'severity' not in report:
        report['severity'] = 'medium'
    
    try:
        response = requests.post(f'{base_url}/upsert', json=report)
        response.raise_for_status()
        upserted_count += 1
        print(f"✅ Upserted {report['id']}: {report['title'][:60]}...")
    except Exception as e:
        print(f"❌ Failed to upsert {report['id']}: {e}")

print(f"\n📊 Successfully upserted {upserted_count}/{len(bug_reports)} bug reports")

