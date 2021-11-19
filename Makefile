# Copyright 2005-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

ENV := $(CURDIR)/env
PIP_CACHE = $(CURDIR)/pip-cache

PYTHON := $(ENV)/bin/python
PSERVE := $(ENV)/bin/pserve
FLAKE8 := $(ENV)/bin/flake8
CELERY := $(ENV)/bin/celery
PIP := $(ENV)/bin/pip
VIRTUALENV := /usr/bin/virtualenv
VENV_ARGS := -p python3

DEPENDENCIES_URL := https://git.launchpad.net/~canonical-launchpad-branches/turnip/+git/dependencies
PIP_SOURCE_DIR := dependencies

# virtualenv and pip fail if setlocale fails, so force a valid locale.
PIP_ENV := LC_ALL=C.UTF-8
# "make PIP_QUIET=0" causes pip to be verbose.
PIP_QUIET := 1
PIP_ENV += PIP_QUIET=$(PIP_QUIET)
PIP_FIND_LINKS := file://$(PIP_CACHE)/
ifneq ($(PIP_SOURCE_DIR),)
PIP_ENV += PIP_NO_INDEX=1
PIP_FIND_LINKS += file://$(shell readlink -f $(PIP_SOURCE_DIR))/
endif
PIP_ENV += PIP_FIND_LINKS="$(PIP_FIND_LINKS)"

# Create archives in labelled directories (e.g.
# <rev-id>/$(PROJECT_NAME).tar.gz)
TARBALL_BUILD_LABEL ?= $(shell git rev-parse HEAD)
TARBALL_FILE_NAME = turnip.tar.gz
TARBALL_BUILDS_DIR ?= build
TARBALL_BUILD_DIR = $(TARBALL_BUILDS_DIR)/$(TARBALL_BUILD_LABEL)
TARBALL_BUILD_PATH = $(TARBALL_BUILD_DIR)/$(TARBALL_FILE_NAME)

SWIFT_CONTAINER_NAME ?= turnip-builds
# This must match the object path used by install_payload in the turnip-base
# charm layer.
SWIFT_OBJECT_PATH = turnip-builds/$(TARBALL_BUILD_LABEL)/$(TARBALL_FILE_NAME)

build: $(ENV)

$(PIP_SOURCE_DIR):
	git clone $(DEPENDENCIES_URL) $(PIP_SOURCE_DIR)

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
	 echo 'find_links = file://$(realpath $(PIP_SOURCE_DIR))/') \
		>$(ENV)/.pydistutils.cfg
	$(PIP_ENV) $(VIRTUALENV) $(VENV_ARGS) --never-download $(ENV)
	$(PIP_ENV) $(PIP) install -r bootstrap-requirements.txt
	$(PIP_ENV) $(PIP) install -c requirements.txt \
		-e '.[test,deploy]'

bootstrap-test: PATH := /usr/sbin:/sbin:$(PATH)
bootstrap-test:
	-sudo rabbitmqctl delete_vhost turnip-test-vhost
	-sudo rabbitmqctl add_vhost turnip-test-vhost
	-sudo rabbitmqctl set_permissions -p "turnip-test-vhost" "guest" ".*" ".*" ".*"

test: $(ENV) bootstrap-test
	$(PYTHON) -m unittest discover $(ARGS) turnip

clean:
	find turnip -name '*.py[co]' -exec rm '{}' \;
	rm -rf $(ENV) $(PIP_CACHE)
	rm -f turnip/version_info.py

dist:
	python3 ./setup.py sdist

TAGS:
	ctags -e -R turnip

tags:
	ctags -R turnip

lint: $(ENV)
	@$(FLAKE8) --exclude=__pycache__,version_info.py *.tac turnip
	$(PYTHON) setup.py check --restructuredtext --strict

pip-check: $(ENV)
	$(PIP) check

check: pip-check test lint

run-api: $(ENV)
	$(PSERVE) api.ini --reload

run-pack: $(ENV)
	$(PYTHON) turnipserver.py

run-worker: $(ENV)
	$(CELERY) -A turnip.tasks worker -n default-worker \
		--loglevel=debug \
		--concurrency=20 \
		--pool=gevent \
		--prefetch-multiplier=1 \
		--queue=celery

run-repack-worker: $(ENV)
	$(CELERY) -A turnip.tasks worker -n repack-worker \
		--loglevel=debug \
		--concurrency=1 \
		--prefetch-multiplier=1 \
		--queue=repacks \
		-O=fair

run:
	make run-api &\
	make run-pack &\
	make run-repack-worker&\
	make run-worker&\
	wait;

stop:
	-pkill -f 'make run-api'
	-pkill -f 'make run-pack'
	-pkill -f 'make run-worker'
	-pkill -f 'make run-repack-worker'	
	-pkill -f '$(CELERY) -A turnip.tasks worker default-worker'
	-pkill -f '$(CELERY) -A turnip.tasks worker repack-worker'

$(PIP_CACHE): $(ENV)
	mkdir -p $(PIP_CACHE)
	$(PIP_ENV) $(PIP) install -d $(PIP_CACHE) \
		-r bootstrap-requirements.txt \
		-r requirements.txt

# XXX cjwatson 2015-10-16: limit to only interesting files
build-tarball: $(PIP_SOURCE_DIR)
	@echo "Creating deployment tarball at $(TARBALL_BUILD_PATH)"
	rm -rf $(PIP_CACHE)
	$(MAKE) $(PIP_CACHE)
	mkdir -p $(TARBALL_BUILD_DIR)
	tar -czf $(TARBALL_BUILD_PATH) \
		--exclude-vcs \
		--exclude build \
		--exclude charm \
		--exclude dist \
		--exclude env \
		./

publish-tarball: build-tarball
	[ ! -e ~/.config/swift/turnip ] || . ~/.config/swift/turnip; \
	./publish-to-swift --debug \
		$(SWIFT_CONTAINER_NAME) $(SWIFT_OBJECT_PATH) \
		$(TARBALL_BUILD_PATH) turnip=$(TARBALL_BUILD_LABEL)

.PHONY: build check clean dist lint run-api run-pack test
.PHONY: build-tarball publish-tarball
