# Copyright 2005-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

PYTHON=python
PSERVE=pserve
FLAKE8=flake8

check:
	$(PYTHON) -m unittest discover turnip

clean:
	find turnip -name '*.py[co]' -exec rm '{}' \;

dist:
	$(PYTHON) ./setup.py sdist

TAGS:
	ctags -e -R turnip

tags:
	ctags -R turnip

lint:
	@$(FLAKE8) turnip

run-api:
	$(PSERVE) api.ini --reload

.PHONY: check clean dist lint run-api
