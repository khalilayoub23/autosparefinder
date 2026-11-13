import supplier_qualification as Q


def test_truncated_tld_is_not_a_contact():
    assert not Q.valid_email("management@autoworks.co.i")
    assert not Q.valid_email("a@b")
    assert Q.valid_email("sales@autoworks.co.il")


def test_verified_contacts_filters_invalid():
    rec = Q.new_record("x")
    Q.set_requirement(rec, "contacts", Q.VERIFIED, evidence_kind="official_policy_page", source="https://e/c",
                      note="n", details={"contacts": [{"address": "m@a.co.i", "status": Q.VERIFIED, "source": "https://e/c"},
                                                      {"address": "m@a.co.il", "status": Q.VERIFIED, "source": "https://e/c"}]})
    assert [c["address"] for c in Q.verified_contacts(rec)] == ["m@a.co.il"]
