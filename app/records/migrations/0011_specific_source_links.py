"""Records point to the most specific place at their source.

- GIS features: the layer's query for that one object, as an HTML page on the publisher's
  server (harvest.store.feature_url), instead of nothing.
- Vendor checkbook rows from the archived repository: the file they came from in it
  (south-dakota-vendor-checkbook at aba4b2b, data/<file>), instead of "file:<file>".
- Roll calls and sessions: the vote's and the session's pages on sdlegislature.gov, instead of
  the API's JSON. (Their sources give these links from now on; this updates what's stored.)
"""

from django.db import migrations

CHECKBOOK = ("https://github.com/south-dakota-citizen-archivist/south-dakota-vendor-checkbook/"
             "blob/aba4b2ba94afdd22e43bd8900cd349d7c4b6205d/data/")  # fmt: skip

FORWARD = f"""
UPDATE records_record r
SET source_url = rtrim(d.url, '/') || '/query?objectIds=' || r.key || '&outFields=*&f=html'
FROM records_recordset rs JOIN census_dataset d ON d.id = rs.gis_dataset_id
WHERE r.recordset_id = rs.id AND rs.ingest = 'gis' AND r.key ~ '^[0-9]+$';

UPDATE records_record r SET source_url = '{CHECKBOOK}' || substr(r.source_url, 6)
FROM records_recordset rs
WHERE r.recordset_id = rs.id AND rs.slug = 'vendor-checkbook' AND r.source_url LIKE 'file:%';

UPDATE records_record r
SET source_url = 'https://sdlegislature.gov/Session/Vote/' || r.key
FROM records_recordset rs
WHERE r.recordset_id = rs.id AND rs.slug = 'legislature-roll-calls'
  AND r.source_url LIKE 'https://sdlegislature.gov/api/Votes/%';

UPDATE records_record r
SET source_url = 'https://sdlegislature.gov/Session/Bills/' || r.key
FROM records_recordset rs
WHERE r.recordset_id = rs.id AND rs.slug = 'legislature-sessions'
  AND r.source_url LIKE 'https://sdlegislature.gov/api/Sessions/%';
"""

BACKWARD = f"""
UPDATE records_record r SET source_url = ''
FROM records_recordset rs WHERE r.recordset_id = rs.id AND rs.ingest = 'gis';

UPDATE records_record r SET source_url = 'file:' || substr(r.source_url, {len(CHECKBOOK) + 1})
FROM records_recordset rs
WHERE r.recordset_id = rs.id AND rs.slug = 'vendor-checkbook'
  AND r.source_url LIKE '{CHECKBOOK}%';

UPDATE records_record r SET source_url = 'https://sdlegislature.gov/api/Votes/' || r.key
FROM records_recordset rs
WHERE r.recordset_id = rs.id AND rs.slug = 'legislature-roll-calls';

UPDATE records_record r SET source_url = 'https://sdlegislature.gov/api/Sessions/' || r.key
FROM records_recordset rs
WHERE r.recordset_id = rs.id AND rs.slug = 'legislature-sessions';
"""


class Migration(migrations.Migration):
    dependencies = [("records", "0010_record_link"), ("census", "0013_candidate_last_seen")]

    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
