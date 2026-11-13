# sdnames

Names of people and organizations as public records write them: property owners, filers, lobbyists, candidates. Plain Python, no Django.

```python
import sdnames

sdnames.normalize("José Peña-O'Brien")  # 'JOSE PENA O BRIEN'

sdnames.kind("NUHN, KAREN S REVOCABLE TRUST")  # 'trust'
sdnames.kind("FIRST LUTHERAN CHURCH")  # 'organization'
sdnames.kind("Jeffrey D. Church")  # 'person'

sdnames.parties("PENNOCK, SCOTT R & DOUGLASS, CLYDETTE D CO-TRUSTEES")
# [Party(name='PENNOCK, SCOTT R', role='trustee'), Party(name='DOUGLASS, CLYDETTE D', role='trustee')]
sdnames.parties("SMITH JOHN A & MARY L", order="family_first")
# [Party(name='SMITH, JOHN A', role=None), Party(name='SMITH, MARY L', role=None)]

p = sdnames.person("Elton A. Three Stars Sr.")
p.given, p.middle, p.family, p.suffix  # ('Elton', 'A.', 'Three Stars', 'Sr.')

sdnames.keys(sdnames.person("Bill Schmidt"))
# {'family': 'SCHMIDT', 'family_sound': ..., 'initial': 'B', 'given_forms': {'BILL', 'WILLIAM', ...}}
```

## What it does

- **`normalize(s)`**: uppercase, accents removed, everything but letters and digits to one space. `normalize_sql(name)` returns the same rule as a Postgres function (it needs the `unaccent` extension), so Python and SQL agree on Latin-script names.
- **`kind(s)`**: person, people (more than one person), organization, government, trust or estate. Words that are also surnames (CHURCH, LODGE) count as organization words only where they aren't a family name. `ftm_schema(s)` gives the matching Follow the Money schema.
- **`parties(s, order=)`**: an owner or filer string split into one party per name, so each can be matched to a different entity on its own.
  - A shared family name is carried to the names that leave it off (KOLBE, JANE B & ROBERT E).
  - Role words are kept as roles: trustee, et al (`others`), et ux (`spouse`), joint tenants, life estate, care of (C/O, %).
  - Organizations, governments, trusts and estates stay one party.
- **`person(s, order=)`**: one person's name in parts, through [nameparser](https://github.com/derek73/python-nameparser).
  - A comma means family first. Pass `order="family_first"` for sources that write LAST FIRST MIDDLE without one.
  - Compound surnames (BRINGS PLENTY, TWO BULLS, TAKES THE SHIELD, and hyphenated ones like BORDEAUX-BLACK BEAR) are read as family names, from a reviewed list in `surnames.txt`.
  - Particle surnames (VAN CAMP, MC PHERSON, ST JOHN) need no list.
- **`org_key(s)`**: an organization's name for matching, with legal forms (LLC, INC, CORP...) and a leading THE removed.
- **`keys(person)`**: blocking keys for finding records worth comparing: family name, its metaphone and NYSIIS codes, first initial, and the given name with the names it's a nickname of. Nickname forms are broad on purpose: they find candidates, they don't decide matches.
- **`surname_candidates(names)`**: multi-word family names found in a list of names, with counts, for review before adding to `surnames.txt`.

## Limits

- Two ordinary surnames side by side without a hyphen (HERSETH SANDLIN) read as a middle name and a family name, since the first word could be either. Compare given and middle names together when matching.
- Compound given names (Red Dawn, Mary Jo) are split into given and middle names.

## Use from another repo

```toml
[project]
dependencies = ["sdnames"]

[tool.uv.sources]
sdnames = { git = "https://github.com/south-dakota-citizen-archivist/sd-archive", subdirectory = "packages/sdnames", tag = "sdnames-vX.Y.Z" }
```
