import pytest

from larder.units import convert


@pytest.mark.parametrize("q,f,t,want", [(750, "g", "kg", 0.75), (1.5, "tsp", "ml", 7.5), (1, "tsp", "tbsp", 1 / 3),
                                        (2, "cup", "l", 0.48), (0.25, "l", "tbsp", 50 / 3), (3, "PCS", "pcs", 3),
                                        (1, "KG", "g", 1000)])
def test_exact(q, f, t, want):
    assert convert(q, f, t) == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize("f,t", [("g", "ml"), ("cup", "kg"), ("pcs", "g"), ("l", "pcs")])
def test_families(f, t):
    with pytest.raises(ValueError):
        convert(1, f, t)
