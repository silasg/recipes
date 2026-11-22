"""Keep the fork's default-unit relation within upstream's nested-write scope."""
import json

import pytest
from django.contrib import auth
from django.urls import reverse
from django_scopes import scopes_disabled

from cookbook.models import Unit


@pytest.mark.parametrize('nested', [False, True])
def test_default_unit_update_is_scoped(space_1, space_2, a1_s1, nested):
    space_1.created_by = auth.get_user(a1_s1)
    space_1.save()
    with scopes_disabled():
        own_unit = Unit.objects.create(name='Own unit', space=space_1)
        foreign_unit = Unit.objects.create(name='Foreign unit', space=space_2)

    url = reverse('api:space-detail', args=[space_1.id])
    own_value = {'id': own_unit.id, 'name': own_unit.name} if nested else own_unit.id
    response = a1_s1.patch(url, {'default_unit': own_value}, content_type='application/json')
    assert response.status_code == 200
    assert json.loads(response.content)['default_unit']['id'] == own_unit.id
    space_1.refresh_from_db()
    assert space_1.default_unit_id == own_unit.id

    foreign_value = {'id': foreign_unit.id, 'name': 'Unauthorized rename'} if nested else foreign_unit.id
    response = a1_s1.patch(url, {'default_unit': foreign_value}, content_type='application/json')
    assert response.status_code == 200
    space_1.refresh_from_db()
    assert space_1.default_unit_id == own_unit.id
    with scopes_disabled():
        foreign_unit.refresh_from_db()
        assert foreign_unit.name == 'Foreign unit'

    response = a1_s1.patch(url, {'default_unit': None}, content_type='application/json')
    assert response.status_code == 200
    assert json.loads(response.content)['default_unit'] is None
    space_1.refresh_from_db()
    assert space_1.default_unit_id is None
