# The charm tool is shipped as a snap, so make sure it's on $PATH.
export PATH := $(PATH):/snap/bin

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

PUBLISH_REPO_PREFIX := lp:~canonical-launchpad-branches/turnip/+git/charm-build-
PUBLISHDIR := $(BUILDDIR)/publish
# We may need to force username and email when publishing, because git may
# not be able to autodetect this in automatic build environments.
DOMAIN ?= $(shell hostname -f)
GIT_USERNAME = $(shell git config --get user.name || echo $(USER))
GIT_EMAIL = $(shell git config --get user.email || echo $(USER)@$(DOMAIN))

all: build lint

$(BUILDDIR) $(TMPDIR) $(PUBLISHDIR):
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
	@rm -rf dist/.built-$* dist/builds/$* $(PUBLISHDIR)/$*

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

publish: build lint | $(PUBLISHDIR)
	@set -e; for charm in $(CHARMS); do \
		if [ -d $(PUBLISHDIR)/$$charm ]; then \
			git -C $(PUBLISHDIR)/$$charm pull; \
		else \
			git clone $(PUBLISH_REPO_PREFIX)$$charm \
				$(PUBLISHDIR)/$$charm; \
		fi; \
		rsync -a -m --ignore-times --exclude .git --delete \
			dist/builds/$$charm/ $(PUBLISHDIR)/$$charm/; \
		git -C $(PUBLISHDIR)/$$charm add .; \
		if [ "$$(git -C $(PUBLISHDIR)/$$charm status --porcelain || \
			 echo status failed)" ]; then \
			git -C $(PUBLISHDIR)/$$charm \
				-c user.name="$(GIT_USERNAME)" \
				-c user.email="$(GIT_EMAIL)" \
				commit -a \
				-m "Build of $$charm from $(BUILD_LABEL)"; \
			git -C $(PUBLISHDIR)/$$charm tag build/$(BUILD_LABEL); \
		fi; \
		git -C $(PUBLISHDIR)/$$charm push --tags origin master; \
	done

# Prepare a Jenkins-as-a-service container for charm building.
setup-jenkaas:
	sudo systemctl stop snapd.socket
	sudo systemctl stop snapd
	echo SNAPPY_STORE_NO_CDN=1 | sudo tee -a /etc/environment >/dev/null
	echo SNAPPY_TESTING=1 | sudo tee -a /etc/environment >/dev/null
	sudo systemctl start snapd.socket
	sudo snap install --classic charm

.PHONY: $(foreach charm,$(CHARMS),build-$(charm))
.PHONY: all build clean deploy lint payload publish setup-jenkaas
