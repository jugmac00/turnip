# Copyright 2005-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

ENV := $(CURDIR)/env
PIP_CACHE = $(CURDIR)/pip-cache

PYTHON := $(ENV)/bin/python
PSERVE := $(ENV)/bin/pserve
FLAKE8 := $(ENV)/bin/flake8
PIP := $(ENV)/bin/pip
VIRTUALENV := virtualenv

DEPENDENCIES_URL := https://git.launchpad.net/~canonical-launchpad-branches/turnip/+git/dependencies

PIP_CACHE_ARGS := -q
ifneq ($(PIP_SOURCE_DIR),)
PIP_CACHE_ARGS += --no-index --find-links=file://$(realpath $(PIP_SOURCE_DIR))/
endif

# Create archives in labelled directories (e.g.
# <rev-id>/$(PROJECT_NAME).tar.gz)
TARBALL_BUILD_LABEL ?= $(shell git rev-parse HEAD)
TARBALL_FILE_NAME = turnip.tar.gz
TARBALL_BUILDS_DIR ?= build
TARBALL_BUILD_DIR = $(TARBALL_BUILDS_DIR)/$(TARBALL_BUILD_LABEL)
TARBALL_BUILD_PATH = $(TARBALL_BUILD_DIR)/$(TARBALL_FILE_NAME)

build: $(ENV)

bootstrap:
	if [ -d dependencies ]; then \
		git -C dependencies pull; \
	else \
		git clone $(DEPENDENCIES_URL) dependencies; \
	fi
	$(MAKE) PIP_SOURCE_DIR=dependencies

turnip/version_info.py:
	echo 'version_info = {"revision_id": "$(TARBALL_BUILD_LABEL)"}' >$@

$(ENV): turnip/version_info.py
ifeq ($(PIP_SOURCE_DIR),)
	@echo "Set PIP_SOURCE_DIR to the path of a clone of" >&2
	@echo "$(DEPENDENCIES_URL)." >&2
	@exit 1
endif
	mkdir -p $(ENV)
	(echo '[easy_install]'; \
	 echo "allow_hosts = ''"; \
	 echo 'find_links = file://$(realpath $(PIP_SOURCE_DIR))/') \
		>$(ENV)/.pydistutils.cfg
	$(VIRTUALENV) --never-download $(ENV)
	$(PIP) install $(PIP_CACHE_ARGS) -r bootstrap-requirements.txt
	$(PIP) install $(PIP_CACHE_ARGS) -c requirements.txt \
		-e '.[test,deploy]'

check: $(ENV)
	$(PYTHON) -m unittest discover $(ARGS) turnip

clean:
	find turnip -name '*.py[co]' -exec rm '{}' \;
	rm -rf $(ENV) $(PIP_CACHE)
	rm -f turnip/version_info.py

dist:
	python ./setup.py sdist

TAGS:
	ctags -e -R turnip

tags:
	ctags -R turnip

lint: $(ENV)
	@$(FLAKE8) --exclude=__pycache__,version_info.py turnip
	$(PYTHON) setup.py check --restructuredtext --strict

run-api: $(ENV)
	$(PSERVE) api.ini --reload

$(PIP_CACHE): $(ENV)
	mkdir -p $(PIP_CACHE)
	$(PIP) install $(PIP_CACHE_ARGS) -d $(PIP_CACHE) \
		-r bootstrap-requirements.txt \
		-r requirements.txt

# XXX cjwatson 2015-10-16: limit to only interesting files
build-tarball:
	@echo "Creating deployment tarball at $(TARBALL_BUILD_PATH)"
	rm -rf $(PIP_CACHE)
	$(MAKE) $(PIP_CACHE)
	mkdir -p $(TARBALL_BUILD_DIR)
	tar -czf $(TARBALL_BUILD_PATH) \
		--exclude-vcs \
		--exclude build \
		--exclude dist \
		--exclude env \
		./

test: $($ENV)
	$(PYTHON) -m unittest discover -v turnip

.PHONY: build check clean dist lint run-api build-tarball
