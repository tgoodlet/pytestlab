#
# Copyright 2017 Sangoma Technologies Inc.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import pytest


@pytest.hookspec
def pytest_lab_configure(envmanager):
    """pytestlab startup"""


@pytest.hookspec(historic=True)
def pytest_lab_addroles(config, rolemanager):
    """new role registered"""


# TODO: Hook for publishing new role **should not** be historic - this
# no longer makes sense. Roles can now disappear before the historic
# hook can be triggered. Any plugin that cares about having a complete
# canonical list of roles should talk directly to the role manager
# instead.
@pytest.hookspec(historic=True)
def pytest_lab_role_created(config, ctl):
    """Called when a new role controller is created (and loaded) at a
    location.
    """


@pytest.hookspec
def pytest_lab_role_destroyed(config, ctl):
    """Called when a role controller is destroyed.
    """


@pytest.hookspec
def pytest_lab_location_destroyed(config, location):
    """Called when a location is released by the environment manager.
    """


@pytest.hookspec
def pytest_lab_add_providers(config, providermanager):
    """Called to enable adding addtional/external environment providers.
    """
