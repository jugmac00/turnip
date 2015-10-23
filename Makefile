# Copyright 2005-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

ENV = $(CURDIR)/env
PIP_CACHE = $(CURDIR)/pip-cache

PYTHON = $(ENV)/bin/python
PSERVE = $(ENV)/bin/pserve
FLAKE8 = $(ENV)/bin/flake8

PIP_CACHE_ARGS := -q --no-use-wheel
ifneq ($(PIP_SOURCE_DIR),)
PIP_CACHE_ARGS += --no-index --find-links=file://$(realpath $(PIP_SOURCE_DIR))/
endif

# Create archives in labelled directories (e.g.
# <rev-id>/$(PROJECT_NAME).tar.gz)
TARBALL_BUILD_LABEL ?= $(shell bzr log -rlast: --show-ids | sed -n 's/^revision-id: //p')
TARBALL_FILE_NAME = turnip.tar.gz
TARBALL_BUILDS_DIR ?= build
TARBALL_BUILD_DIR = $(TARBALL_BUILDS_DIR)/$(TARBALL_BUILD_LABEL)
TARBALL_BUILD_PATH = $(TARBALL_BUILD_DIR)/$(TARBALL_FILE_NAME)

build: $(ENV)

$(ENV):
	mkdir -p $(ENV)
ifneq ($(PIP_SOURCE_DIR),)
	(echo '[easy_install]'; \
	 echo "allow_hosts = ''"; \
	 echo 'find_links = file://$(realpath $(PIP_SOURCE_DIR))/') \
		>$(ENV)/.pydistutils.cfg
endif
	virtualenv $(ENV)
	$(ENV)/bin/pip install $(PIP_CACHE_ARGS) \
		-r bootstrap-requirements.txt
	$(ENV)/bin/pip install $(PIP_CACHE_ARGS) \
		-r requirements.txt \
		-r deploy-requirements.txt \
		-r test-requirements.txt \
		-e .

check: $(ENV)
	$(PYTHON) -m unittest discover turnip

clean:
	find turnip -name '*.py[co]' -exec rm '{}' \;
	rm -rf $(ENV) $(PIP_CACHE)

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

$(PIP_CACHE): $(ENV)
	mkdir -p $(PIP_CACHE)
	$(ENV)/bin/pip install $(PIP_CACHE_ARGS) -d $(PIP_CACHE) \
		-r bootstrap-requirements.txt \
		-r requirements.txt \
		-r deploy-requirements.txt \
		-r test-requirements.txt

# XXX cjwatson 2015-10-16: limit to only interesting files
build-tarball:
ifeq ($(PIP_SOURCE_DIR),)
	@echo "Set PIP_SOURCE_DIR to the path of a checkout of" >&2
	@echo "lp:~canonical-launchpad-branches/turnip/dependencies." >&2
	@exit 1
endif
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

.PHONY: build check clean dist lint run-api build-tarball
