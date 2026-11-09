"""car-parts.ie brand mapping (#140 option B): existing, alias, creation, duplicate prevention, unknown brands, idempotency."""
import asyncio

import car_brand_resolver as cbr

BR = {  # key -> (id, name, active)
    "toyota": ("id-toyota", "Toyota", True),
    "volkswagen": ("id-vw", "Volkswagen", True),
    "vw": ("id-vwdup", "Vw", True),               # the production duplicate record - overrides must still pick Volkswagen
    "mercedesbenz": ("id-mb", "Mercedes-Benz", True),
    "mazda": ("id-mazda", "Mazda", True),
    "jaguar": ("id-jag", "Jaguar", True),
    "dsautomobiles": ("id-ds", "DS Automobiles", True),
    "ford": ("id-ford", "Ford", True),
    "iveco": ("id-iveco", "Iveco", False),
    "citroen": ("id-cit", "Citroen", True),
}


def plan(src, verified=True, aliases=None):
    return cbr.plan_resolution(src, BR, aliases or {}, verified)


def test_existing_brand_by_normalized_name():
    assert plan("Citroën")["id"] == "id-cit" and plan("mazda")["id"] == "id-mazda"      # mazda no longer lands on Jaguar
    assert plan("Mercedes-Benz")["action"] == "existing"


def test_alias_overrides_pick_the_canonical_record_not_the_duplicate():
    assert plan("vw")["id"] == "id-vw" and plan("mercedes")["id"] == "id-mb" and plan("ds")["id"] == "id-ds"
    assert plan("ford-usa")["id"] == "id-ford" and plan("Ford Otosan")["id"] == "id-ford"


def test_brand_aliases_table_is_used():
    assert plan("holden-x", aliases={"holdenx": ("id-h", "Holden")})["id"] == "id-h"


def test_inactive_record_reused_not_reactivated():
    p = plan("iveco")
    assert p["id"] == "id-iveco" and p["inactive"] is True


def test_unknown_but_site_listed_brand_is_created():
    p = plan("Alfa Romeo-x")
    assert p["action"] == "create" and p["name"] == "Alfa Romeo X"
    assert plan("ktm")["name"] == "KTM" and plan("austin-healey")["name"] == "Austin Healey"


def test_unverifiable_or_empty_brand_is_unresolved_never_guessed():
    assert plan("zzbrand", verified=False)["action"] == "unresolved"
    assert plan("")["action"] == "unresolved" and plan("Unknown")["action"] == "unresolved"
    assert plan("bad brand!!!", verified=True)["action"] in ("create", "unresolved")     # slugified; never raw text


def test_distinct_marques_not_aliased():
    assert plan("vauxhall")["action"] == "create" and plan("austin")["action"] == "create"


class FakeConn:
    """Minimal asyncpg stand-in: car_brands + brand_aliases + harvest_queue."""
    def __init__(self):
        self.brands = {k: v for k, v in BR.items() if k != "vw"}
        self.rows = [{"id": v[0], "name": v[1], "is_active": v[2], "aliases": []} for v in self.brands.values()]
        self.alias_rows = []
        self.site = ["Vauxhall", "Toyota", "KTM"]
        self.inserts = 0

    async def fetch(self, sql, *a):
        if "FROM car_brands" in sql:
            return self.rows
        if "FROM brand_aliases" in sql:
            return [{"normalized": n, "brand_id": b, "name": nm} for n, b, nm in self.alias_rows]
        if "harvest_queue" in sql:
            return [{"brand_en": b} for b in self.site]
        raise AssertionError(sql)

    async def execute(self, sql, *a):
        if sql.startswith("INSERT INTO car_brands"):
            if not any(r["name"].lower() == a[0].lower() for r in self.rows):
                self.rows.append({"id": f"new-{a[0]}", "name": a[0], "is_active": True, "aliases": [a[1]]})
                self.inserts += 1
        elif sql.startswith("INSERT INTO brand_aliases"):
            self.alias_rows.append((a[2], a[0], next(r["name"] for r in self.rows if r["id"] == a[0])))


def test_creation_is_idempotent_and_records_alias():
    c = FakeConn()
    r1 = asyncio.run(cbr.resolve_source_brand(c, "vauxhall"))
    r2 = asyncio.run(cbr.resolve_source_brand(c, "Vauxhall"))          # second import, fresh cache
    r3 = asyncio.run(cbr.resolve_source_brand(c, "vauxhall", {}))
    assert r1.action == "created" and r1.name == "Vauxhall"
    assert r2.brand_id == r1.brand_id == r3.brand_id and c.inserts == 1
    assert r2.action in ("existing", "alias")


def test_unlisted_brand_creates_nothing():
    c = FakeConn()
    r = asyncio.run(cbr.resolve_source_brand(c, "zzbrand"))
    assert r.action == "unresolved" and c.inserts == 0


def test_summary_counts():
    rs = [cbr.BrandResolution("existing", "1", "a", "a"), cbr.BrandResolution("created", "2", "b", "b"),
          cbr.BrandResolution("unresolved", None, None, "c"), cbr.BrandResolution("alias", "3", "d", "d", inactive=True)]
    assert cbr.summarize(rs) == {"existing": 1, "alias": 1, "created": 1, "unresolved": 1, "inactive_reused": 1}
