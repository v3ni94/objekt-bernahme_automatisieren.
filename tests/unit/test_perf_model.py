"""Unit-Tests fuer das Performance-Rechenmodell (Umsetzungsplan 2.8)."""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "performance"))

import perf_model as pm  # noqa: E402


def test_basisfall_cpu_sekunden_wie_im_plan():
    # Basis: 10.000 Seiten, 40 Prozent digital, t_ocr 3,5 s, t_txt 0,1 s
    assert pm.cpu_seconds(10_000, 0.40, 3.5, 0.1) == pytest.approx(21_400.0)


def test_unguenstiger_fall_ohne_digitalseiten():
    assert pm.cpu_seconds(10_000, 0.0, 3.5, 0.1) == pytest.approx(35_000.0)


def test_wandzeit_mit_drei_prozessen_haelt_nur_ohne_reserve():
    wall = pm.wall_seconds(21_400.0, 3, 0.85)
    assert wall == pytest.approx(8392.16, rel=1e-4)
    assert wall > pm.OCR_BUDGET_SECONDS  # verfehlt das OCR-Budget von 2 h
    assert wall < pm.TOTAL_BUDGET_SECONDS  # haelt die 3-Stunden-Vorgabe knapp


def test_wandzeit_mit_fuenf_prozessen_haelt_mit_reserve():
    assert pm.wall_seconds(21_400.0, 5, 0.85) < pm.OCR_BUDGET_SECONDS


def test_p_min_basisfall_ergibt_vier_prozesse():
    assert pm.p_min(21_400.0, 0.85) == 4  # 3,5 aufgerundet


def test_p_min_unguenstiger_fall_ergibt_sechs_prozesse():
    assert pm.p_min(35_000.0, 0.85) == 6  # 5,7 aufgerundet


def test_skalierungseffizienz():
    # 1 Prozess: 0,3 Seiten/s; 4 Prozesse: 1,02 Seiten/s -> e = 0,85
    assert pm.scaling_efficiency(0.3, 1.02, 4) == pytest.approx(0.85)


def test_szenariotabelle_markiert_verfehlte_zellen():
    rows = pm.scenario_table(
        [pm.Scenario("Basis", 0.40, 3.5, 0.1), pm.Scenario("Ungünstig", 0.0, 3.5, 0.1)], [3, 5, 7], 0.85
    )
    basis, unguenstig = rows
    assert basis["cells"][3]["within_total_budget"] is True
    assert basis["cells"][5]["within_ocr_budget"] is True
    assert unguenstig["cells"][3]["within_total_budget"] is False  # rund 229 min
    assert unguenstig["cells"][5]["within_total_budget"] is True
    assert math.isclose(unguenstig["cells"][3]["wall_minutes"], 228.8, abs_tol=0.5)


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_digitalanteil_wird_geprueft(bad):
    with pytest.raises(ValueError):
        pm.cpu_seconds(100, bad, 3.0, 0.1)
