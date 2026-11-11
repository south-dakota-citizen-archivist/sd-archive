import pytest

import sdnames as s


def parts(p: s.Person) -> tuple[str, str, str, str]:
    return p.given, p.middle, p.family, p.suffix


@pytest.mark.parametrize(
    "value, expected",
    [
        ("  José  Peña ", "JOSE PENA"),
        ("O'Brien–Smith", "O BRIEN SMITH"),
        ("Straße Øster Æsir", "STRASSE OSTER AESIR"),
        ("SMITH™", "SMITH"),
        ("a\tb", "A B"),
        ("---", ""),
        (None, None),
    ],
)
def test_normalize(value, expected):
    assert s.normalize(value) == expected


def test_normalize_sql_names_the_function():
    assert "CREATE OR REPLACE FUNCTION my_norm(value text)" in s.normalize_sql("my_norm")
    with pytest.raises(ValueError):
        s.normalize_sql("x; DROP TABLE y")


@pytest.mark.parametrize(
    "text, order, expected",
    [
        ("JOHN BRINGS PLENTY", "auto", ("JOHN", "", "BRINGS PLENTY", "")),
        ("JAMES TWO BULLS JR", "auto", ("JAMES", "", "TWO BULLS", "JR")),
        ("Elton A. Three Stars Sr.", "auto", ("Elton", "A.", "Three Stars", "Sr.")),
        ("Jennifer Her Many Horses", "auto", ("Jennifer", "", "Her Many Horses", "")),
        ("Jennifer Bordeaux-Black Bear", "auto", ("Jennifer", "", "Bordeaux-Black Bear", "")),
        ("Anna Diaz-Takes The Shield", "auto", ("Anna", "", "Diaz-Takes The Shield", "")),
        ("Elizabeth Little Eagle", "auto", ("Elizabeth", "", "Little Eagle", "")),
        ("Eli John Little", "auto", ("Eli", "John", "Little", "")),
        ("MARY VAN CAMP", "auto", ("MARY", "", "VAN CAMP", "")),
        ("WILSON, NICOLE", "auto", ("NICOLE", "", "WILSON", "")),
        ("SMITH JR, JOHN A", "auto", ("JOHN", "A", "SMITH", "JR")),
        ("REBECCA CHRISTIANSEN, LPC-MH, LPC, NCC", "auto",
         ("REBECCA", "", "CHRISTIANSEN", "LPC-MH, LPC, NCC")),
        ("HEMINGWAY-", "auto", ("HEMINGWAY", "", "", "")),
        ("SMITH JOHN A", "family_first", ("JOHN", "A", "SMITH", "")),
        ("SMITH JR JOHN A", "family_first", ("JOHN", "A", "SMITH", "JR")),
        ("VAN DE WEGHE MARK", "family_first", ("MARK", "", "VAN DE WEGHE", "")),
        ("BRINGS PLENTY JOHN", "family_first", ("JOHN", "", "BRINGS PLENTY", "")),
        ("LEWIS,JAMES CARL  ETUX", "auto", ("JAMES", "CARL", "LEWIS", "")),
        ("SMITH JOHN C/O ACME BANK", "family_first", ("JOHN", "", "SMITH", "")),
    ],
)  # fmt: skip
def test_person(text, order, expected):
    assert parts(s.person(text, order)) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("JOHN SMITH", "person"),
        ("Jeffrey D. Church", "person"),
        ("CHURCH, JEFFREY D", "person"),
        ("IRA SMITH", "person"),
        ("KOLBE, JANE B & ROBERT E", "people"),
        ("FIELDS,LYNN M  ETUX", "people"),
        ("SMITH JOHN C/O ACME BANK", "person"),
        ("BLACK HILLS CORP", "organization"),
        ("FIRST LUTHERAN CHURCH", "organization"),
        ("OAKRIDGE ESTATES HOMEOWNERS", "organization"),
        ("ABC REAL ESTATE", "organization"),
        ("WONDERLAND HOMES WATER &", "organization"),
        ("CITY OF SPEARFISH", "government"),
        ("OGLALA SIOUX TRIBE", "government"),
        ("USA IN TRUST FOR CHEYENNE-", "government"),
        ("NUHN, KAREN S REVOCABLE TRUST", "trust"),
        ("ALBERTSON FAMILY TRU", "trust"),
        ("SOBIN,ALAN J & TERRY L LIVING", "trust"),
        ("ESTATE OF JOHN SMITH", "estate"),
    ],
)
def test_kind(text, expected):
    assert s.kind(text) == expected


def names(text, order="auto"):
    return [(p.name, p.role) for p in s.parties(text, order)]


def test_parties_share_a_family_name():
    assert names("KOLBE, JANE B & ROBERT E") == [("KOLBE, JANE B", None), ("KOLBE, ROBERT E", None)]
    assert names("JOHNSON, DAVID J OR PATRICIA J") == [
        ("JOHNSON, DAVID J", None), ("JOHNSON, PATRICIA J", None)]  # fmt: skip
    assert names("SMITH JOHN A & MARY L", "family_first") == [
        ("SMITH, JOHN A", None), ("SMITH, MARY L", None)]  # fmt: skip
    assert names("BRINGS PLENTY JOHN & MARY", "family_first") == [
        ("BRINGS PLENTY, JOHN", None), ("BRINGS PLENTY, MARY", None)]  # fmt: skip
    assert names("JOHN AND MARY SMITH") == [("SMITH, JOHN", None), ("MARY SMITH", None)]


def test_parties_two_people_stay_two_people():
    # each becomes its own mention, so each can match a different entity
    assert names("HENRY JAMES AND FRANK OCEAN") == [("HENRY JAMES", None), ("FRANK OCEAN", None)]
    got = [s.person(n) for n, _ in names("HENRY JAMES AND FRANK OCEAN")]
    assert [(p.given, p.family) for p in got] == [("HENRY", "JAMES"), ("FRANK", "OCEAN")]


def test_parties_split_a_given_first_list_on_commas():
    claimants = "Anita Resenstein, Fayez Sarofim, Stuart Korshak, James Greenbaum"
    assert names(claimants, "given_first") == [
        ("Anita Resenstein", None),
        ("Fayez Sarofim", None),
        ("Stuart Korshak", None),
        ("James Greenbaum", None),
    ]
    assert names("Clyde Fessler, Jr., Ann Lee, III and Bo Day", "given_first") == [
        ("Clyde Fessler, Jr.", None),
        ("Ann Lee, III", None),
        ("Bo Day", None),
    ]
    assert names("Banks, Chris") == [("Banks, Chris", None)]  # a comma means family first


def test_parties_roles():
    assert names("PENNOCK, SCOTT R & DOUGLASS, CLYDETTE D CO-TRUSTEES") == [
        ("PENNOCK, SCOTT R", "trustee"), ("DOUGLASS, CLYDETTE D", "trustee")]  # fmt: skip
    assert names("THOMPSON,DANA L  ETAL") == [("THOMPSON,DANA L", None), (None, "others")]
    assert names("LEWIS,JAMES CARL  ETUX") == [("LEWIS,JAMES CARL", None), (None, "spouse")]
    assert names("SMITH JOHN C/O ACME BANK", "family_first") == [
        ("SMITH, JOHN", None), ("ACME BANK", "care of")]  # fmt: skip
    assert names("% SHIRLEY K DUNLAP") == [("SHIRLEY K DUNLAP", "care of")]


def test_parties_keep_organizations_whole():
    assert names("SMITH & SONS CONSTRUCTION LLC") == [("SMITH & SONS CONSTRUCTION LLC", None)]
    assert names("HOFFMAN,EDITH G LIVING TRUST") == [("HOFFMAN,EDITH G LIVING TRUST", None)]


def test_org_key():
    assert s.org_key("Black Hills Corporation") == s.org_key("BLACK HILLS CORP") == "BLACK HILLS"
    assert s.org_key("The Acme Co., L.L.C.") == "ACME"


def test_ftm_schema():
    assert s.ftm_schema("JOHN SMITH") == "Person"
    assert s.ftm_schema("ZBR INVESTMENTS LLC") == "Company"
    assert s.ftm_schema("FIRST LUTHERAN CHURCH") == "Organization"
    assert s.ftm_schema("CITY OF SPEARFISH") == "PublicBody"
    assert s.ftm_schema("NUHN, KAREN S REVOCABLE TRUST") == "LegalEntity"


def test_keys():
    k = s.keys(s.person("Bill Schmidt"))
    assert k["family"] == "SCHMIDT" and k["initial"] == "B"
    assert {"BILL", "WILLIAM"} <= k["given_forms"]
    assert k["family_sound"] and k["family_nysiis"]
    assert s.keys(s.person("TWO BULLS, JAMES"))["family"] == "TWO BULLS"


def test_compound_surnames_listed_and_found():
    assert ("BRINGS", "PLENTY") in s.compound_surnames()
    found = s.surname_candidates(
        ["Robert Two Crow", "Mary Shoots The Enemy", "John A. Smith", "Mary Jo Smith",
         "Lyle Van Hove", "SMITH, MARY JO", "Jo Smith"]
    )  # fmt: skip
    assert "SHOOTS THE ENEMY" in found
    assert "TWO CROW" not in found  # already listed
    assert "VAN HOVE" not in found  # a particle name
    assert "JO SMITH" not in found  # a double given name
