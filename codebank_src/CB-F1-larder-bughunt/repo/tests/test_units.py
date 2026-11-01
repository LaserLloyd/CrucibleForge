from larder.units import convert, family


def test_same_unit():
    assert convert(3, "g", "g") == 3


def test_up_to_ml():
    assert convert(2, "tbsp", "ml") == 30
    assert family("Cup") == "volume"
