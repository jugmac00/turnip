# Copyright 2005-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

ENV = $(CURDIR)/env

PYTHON = $(ENV)/bin/python
PSERVE = $(ENV)/bin/pserve
FLAKE8 = $(ENV)/bin/flake8

ifeq ($(PIP_SOURCE_DIR),)
PIP_CACHE_ARGS :=
else
PIP_CACHE_ARGS := --no-index --find-links=file://$(realpath $(PIP_SOURCE_DIR))/
endif

# Create archives in labelled directories (e.g. r182/$(PROJECT_NAME).tar.gz)
TARBALL_BUILD_LABEL ?= r$(shell bzr revno)
TARBALL_FILE_NAME = turnip.tar.gz
TARBALL_BUILDS_DIR ?= build
TARBALL_BUILD_DIR = $(TARBALL_BUILDS_DIR)/$(TARBALL_BUILD_LABEL)
TARBALL_BUILD_PATH = $(TARBALL_BUILD_DIR)/$(TARBALL_FILE_NAME)

$(ENV):
	mkdir -p $(ENV)
ifneq ($(PIP_SOURCE_DIR),)
	(echo '[easy_install]'; \
	 echo "allow_hosts = ''"; \
	 echo 'find_links = file://$(realpath $(PIP_SOURCE_DIR))/') \
		>$(ENV)/.pydistutils.cfg
endif
	virtualenv $(ENV)
	$(ENV)/bin/pip install $(PIP_CACHE_ARGS) --no-use-wheel \
		-r bootstrap-requirements.txt
	$(ENV)/bin/pip install $(PIP_CACHE_ARGS) --no-use-wheel \
		-r requirements.txt \
		-r deploy-requirements.txt \
		-r test-requirements.txt

check: $(ENV)
	$(PYTHON) -m unittest discover turnip

clean:
	find turnip -name '*.py[co]' -exec rm '{}' \;
	rm -rf $(ENV)

dist:
	python ./setup.py sdist

TAGS:
	ctags -e -R turnip

tags:
	ctags -R turnip

lint: $(ENV)
	@$(FLAKE8) turnip

run-api: $(ENV)
	$(PSERVE) api.ini --reload

# XXX cjwatson 2015-10-16: limit to only interesting files
build-tarball: $(ENV)
	@echo "Creating deployment tarball at $(TARBALL_BUILD_PATH)"
	mkdir -p $(TARBALL_BUILD_DIR)
	tar -czf $(TARBALL_BUILD_PATH) \
		--exclude-vcs \
		--exclude build \
		--exclude dist \
		--exclude env/local \
		./

.PHONY: check clean dist lint run-api
