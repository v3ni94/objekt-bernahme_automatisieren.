"""Konsistenzlauf meldet Ueberlappungen als Review-Fall data_consistency, idempotent."""

from __future__ import annotations

from datetime import date

import pytest

from apps.objects.models import ManagedObject, Unit
from apps.parties.models import Owner, OwnerUnitAssignment
from apps.parties.tasks import check_assignment_consistency, run_consistency_check
from apps.review.models import ReviewCase

pytestmark = pytest.mark.django_db


def test_konsistenzlauf_erzeugt_einen_fall_je_paar():
    obj = ManagedObject.objects.create(object_number="624", management_type="weg", is_test=True)
    unit = Unit.objects.create(
        object=obj, unit_label="WE01", unit_label_normalized="WE1", unit_number="1", unit_type="apartment"
    )
    o = Owner.objects.create(type="natural_person", last_name="Mustermann", search_name="MUSTERMANN")
    OwnerUnitAssignment.objects.create(owner=o, unit=unit, valid_from=date(2020, 1, 1))
    OwnerUnitAssignment.objects.create(owner=o, unit=unit, valid_from=date(2021, 1, 1))
    assert run_consistency_check() == {"overlaps": 1, "cases_created": 1}
    assert check_assignment_consistency.apply().get() == {"overlaps": 1, "cases_created": 0}
    fall = ReviewCase.objects.get()
    assert (
        fall.case_type == "data_consistency"
        and fall.case_subtype == "assignment_overlap"
        and fall.object == obj
    )
    assert len(fall.candidates) == 2


def test_konsistenzlauf_ohne_befund():
    assert run_consistency_check() == {"overlaps": 0, "cases_created": 0}
    assert ReviewCase.objects.count() == 0
