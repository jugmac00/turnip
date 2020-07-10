# Copyright 2005-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

ENV := $(CURDIR)/env
PY3_ENV := $(CURDIR)/py3env
PIP_CACHE = $(CURDIR)/pip-cache

PYTHON := $(ENV)/bin/python
PSERVE := $(ENV)/bin/pserve
FLAKE8 := $(ENV)/bin/flake8
CELERY := $(ENV)/bin/celery
PIP := $(ENV)/bin/pip
VIRTUALENV := virtualenv

DEPENDENCIES_URL := https://git.launchpad.net/~canonical-launchpad-branches/turnip/+git/dependencies
PIP_SOURCE_DIR := dependencies

PIP_ARGS ?= --quiet
ifneq ($(PIP_SOURCE_DIR),)
override PIP_ARGS += --no-index --find-links=file://$(realpath $(PIP_SOURCE_DIR))/
endif

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
	$(VIRTUALENV) $(VENV_ARGS) --never-download $(ENV)
	$(PIP) install $(PIP_ARGS) -r bootstrap-requirements.txt
	$(PIP) install $(PIP_ARGS) -c requirements.txt \
		-e '.[test,deploy]'

test-bootstrap:
	-sudo rabbitmqctl delete_vhost test-vhost
	-sudo rabbitmqctl add_vhost test-vhost
	-sudo rabbitmqctl set_permissions -p "test-vhost" "guest" ".*" ".*" ".*"

test: $(ENV) test-bootstrap
	$(PYTHON) -m unittest discover $(ARGS) turnip

clean:
	find turnip -name '*.py[co]' -exec rm '{}' \;
	rm -rf $(ENV) $(PY3_ENV) $(PIP_CACHE)
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

pip-check: $(ENV)
	$(PIP) check

check: pip-check test lint

check-python3:
	$(MAKE) check VENV_ARGS="-p python3" ENV="$(PY3_ENV)"

check-python-compat: check check-python3

run-api: $(ENV)
	$(PSERVE) api.ini --reload

run-pack: $(ENV)
	$(PYTHON) turnipserver.py

run-worker: $(ENV)
	PYTHONPATH="turnip" $(CELERY) -A tasks worker \
		--loglevel=info \
		--concurrency=20 \
		--pool=gevent

run:
	make run-api &\
	make run-pack &\
	make run-worker&\
	wait;

stop:
	-pkill -f 'make run-api'
	-pkill -f 'make run-pack'
	-pkill -f 'make run-worker'
	-pkill -f '$(CELERY) -A tasks worker'



$(PIP_CACHE): $(ENV)
	mkdir -p $(PIP_CACHE)
	$(PIP) install $(PIP_ARGS) -d $(PIP_CACHE) \
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
		--exclude charm \
		--exclude dist \
		--exclude env \
		./

publish-tarball: build-tarball
	[ ! -e ~/.config/swift/turnip ] || . ~/.config/swift/turnip; \
	./publish-to-swift --debug \
		$(SWIFT_CONTAINER_NAME) $(SWIFT_OBJECT_PATH) \
		$(TARBALL_BUILD_PATH)

.PHONY: build check clean dist lint run-api run-pack test
.PHONY: build-tarball publish-tarball
