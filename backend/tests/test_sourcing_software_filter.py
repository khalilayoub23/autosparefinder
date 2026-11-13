from services.supplier_sourcing import looks_like_software_vendor as f

def test_saas_platform_is_rejected():
    assert f("Auto parts dropship automation. Book a demo today. Start your free trial. Pricing plans")

def test_real_store_with_cart_is_kept():
    assert not f("Brake pads. Add to cart. Free trial of our app? book a demo")

def test_single_marketing_phrase_is_not_enough():
    assert not f("Our parts store. Book a demo of the garage tool.")

def test_empty():
    assert not f("")
