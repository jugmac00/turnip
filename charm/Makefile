APP_NAME := turnip

BUILDDIR := $(CURDIR)/dist
TMPDIR := $(CURDIR)/tmp
export LAYER_PATH := $(TMPDIR)/deps/layer
export INTERFACE_PATH := $(TMPDIR)/deps/interface
CHARM_WHEELS_DIR := $(TMPDIR)/deps/charm-wheels

BUILD_LABEL = $(shell git rev-parse HEAD)
TARBALL = $(APP_NAME).tar.gz
ASSET = ../build/$(BUILD_LABEL)/$(TARBALL)

CHARMS := \
	turnip-pack-backend \
	turnip-pack-virt \
	turnip-pack-frontend-git \
	turnip-pack-frontend-ssh \
	turnip-pack-frontend-http \
	turnip-api

all: build lint

$(BUILDDIR) $(TMPDIR):
	@mkdir -p $@

CHARM_DEPS := $(LAYER_PATH)/.done $(INTERFACE_PATH)/.done
$(CHARM_DEPS): $(CURDIR)/dependencies.txt | $(TMPDIR)
	@echo "Fetching dependencies..."
	@mkdir -p $(TMPDIR)/deps
	@cd $(TMPDIR)/deps && codetree $<
	@touch $(CHARM_DEPS)

build: $(foreach charm,$(CHARMS),build-$(charm))

build-turnip-pack-backend: dist/.built-turnip-pack-backend
build-turnip-pack-virt: dist/.built-turnip-pack-virt
build-turnip-pack-frontend-git: dist/.built-turnip-pack-frontend-git
build-turnip-pack-frontend-ssh: dist/.built-turnip-pack-frontend-ssh
build-turnip-pack-frontend-http: dist/.built-turnip-pack-frontend-http
build-turnip-api: dist/.built-turnip-api

dist/.built-%: $(CHARM_DEPS) | $(BUILDDIR)
	@echo "Building $*..."
	@cd $* && \
		PIP_NO_INDEX=true PIP_FIND_LINKS=$(CHARM_WHEELS_DIR) \
		charm build -o $(BUILDDIR)
	@touch $@

clean-%:
	@echo "Cleaning $*..."
	@rm -rf dist/.built-$* dist/builds/$*

tmp/git.launchpad.dev.crt: | $(TMPDIR)
	openssl req -new -nodes -keyout tmp/git.launchpad.dev.key \
		-out tmp/git.launchpad.dev.csr -subj '/CN=git.launchpad.dev'
	openssl x509 -req -days 365 -in tmp/git.launchpad.dev.csr \
		-signkey tmp/git.launchpad.dev.key -out $@

tmp/ssh-host-key: | $(TMPDIR)
	ssh-keygen -t rsa -b 2048 -f $@ -N ''

bundle.yaml: bundle.yaml.in tmp/git.launchpad.dev.crt tmp/ssh-host-key
	sed -e 's/%BUILD_LABEL%/$(BUILD_LABEL)/g' \
	     -e "s/%SSL_KEY%/$$(base64 -w 0 <tmp/git.launchpad.dev.key)/g" \
	     -e "s/%SSL_CERT%/$$(base64 -w 0 <tmp/git.launchpad.dev.crt)/g" \
	     -e "s/%PRIVATE_SSH_KEY%/$$(base64 -w 0 <tmp/ssh-host-key)/g" \
	     -e "s/%PUBLIC_SSH_KEY%/$$(base64 -w 0 <tmp/ssh-host-key.pub)/g" \
	     bundle.yaml.in >bundle.yaml

deploy: build payload bundle.yaml
	@echo "Deploying $(APP_NAME)..."
	@juju deploy ./bundle.yaml
	@echo "You will need to point these to a Launchpad/turnipcake deployment:"
	@echo "  - authentication_endpoint (turnip-pack-frontend-ssh)"
	@echo "  - virtinfo_endpoint (turnip-pack-{backend,virt,frontend-http})"

payload: $(ASSET)
$(ASSET):
	@echo "Building asset for $(BUILD_LABEL)..."
	@$(MAKE) -C .. build-tarball

clean:
	@find . -name \*.pyc -delete
	@find . -depth -name '__pycache__' -exec rm -rf '{}' \;
	@rm -f bundle.yaml
	@rm -f layer/*/codetree-collect-info.yaml
	@rm -rf $(BUILDDIR) $(TMPDIR)

lint: build
	@echo "Linting charms..."
	@set -e; for charm in $(CHARMS); do \
		charm proof dist/builds/$$charm; \
	done
	@echo "Linting python sources..."
	@flake8 layer $(CHARMS)

.PHONY: $(foreach charm,$(CHARMS),build-$(charm))
.PHONY: all build clean deploy lint payload
