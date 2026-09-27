import sys, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
with open('data/changelog.json', encoding='utf-8') as f:
    ch = json.load(f)
for entry in ch[:4]:
    date = entry.get('date')
    refs = entry.get('referee_updates', [])
    print(f'=== {date}: {len(refs)} referee updates ===')
    for r in refs:
        mid = r.get('match_id')
        ref = r.get('new_referee')
        print(f'  {mid} -> {ref}')
