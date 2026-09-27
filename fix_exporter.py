"""Script to add ingested_matches parameter to export_all in web_data_exporter.py"""

with open('engine/web_data_exporter.py', encoding='utf-8') as f:
    content = f.read()

# Fix the export_all signature
idx = content.find('    def export_all(self) -> Path:')
if idx == -1:
    print("ERROR: export_all signature not found")
    idx2 = content.find('def export_all')
    print(repr(content[idx2:idx2+200]))
else:
    # Replace just the signature line
    old_sig = '    def export_all(self) -> Path:'
    new_sig = '    def export_all(self, ingested_matches=None, evaluated_combos=None, new_combos=None) -> Path:'
    content = content.replace(old_sig, new_sig, 1)

    # Now update the record_pipeline_execution call to include the passed-in data
    old_call = '''        self.changelog_mgr.record_pipeline_execution(
            date_str=today_str,
            referee_count=len(new_referee_updates),
            referee_updates=new_referee_updates if new_referee_updates else None,
            notes=["Sincronitzaci\u00f3 de les darreres dades i m\u00e8triques del model."] if not new_referee_updates else None
        )'''

    new_call = '''        self.changelog_mgr.record_pipeline_execution(
            date_str=today_str,
            ingested_matches=ingested_matches or [],
            evaluated_combos=evaluated_combos or [],
            new_combos=new_combos or [],
            referee_count=len(new_referee_updates),
            referee_updates=new_referee_updates if new_referee_updates else None,
            notes=["Sincronitzaci\u00f3 de les darreres dades i m\u00e8triques del model."] if not new_referee_updates else None
        )'''

    if old_call in content:
        content = content.replace(old_call, new_call, 1)
        print("Updated record_pipeline_execution call with match data params")
    else:
        print("WARNING: record_pipeline_execution call pattern not found, checking...")
        idx3 = content.find('referee_updates=new_referee_updates')
        if idx3 != -1:
            print(repr(content[max(0,idx3-300):idx3+300]))

    with open('engine/web_data_exporter.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("File written successfully")
