# Copyright 2005-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

PYTHON = python

BOOTSTRAP_BIN := bootstrap.py
BOOTSTRAP = PYTHONPATH= $(PYTHON) $(BOOTSTRAP_BIN)

BUILDOUT_BIN := bin/buildout
BUILDOUT_CFG := buildout.cfg
BUILDOUT = PYTHONPATH= $(BUILDOUT_BIN) -qc $(BUILDOUT_CFG)


default: check


build: bin/twistd


# When a built tree is moved this updates absolute paths.
build-update-paths:
	$(BUILDOUT)


check: bin/test
	bin/test


dist: $(BUILDOUT_BIN)
	$(BUILDOUT) setup setup.py egg_info -r sdist


TAGS: bin/tags
	bin/tags --ctags-emacs


tags: bin/tags
	bin/tags --ctags-vi


download-cache:
	mkdir -p download-cache


eggs:
	mkdir -p eggs


$(BUILDOUT_BIN): download-cache eggs
	$(BOOTSTRAP) \
	    --setup-source=download-cache/dist/ez_setup.py \
	    --download-base=download-cache/dist \
	    --eggs=eggs --version=1.5.2
	touch --no-create $@


bin/twistd: $(BUILDOUT_BIN) $(BUILDOUT_CFG) setup.py
	$(BUILDOUT) install runtime


bin/test: $(BUILDOUT_BIN) $(BUILDOUT_CFG) setup.py
	$(BUILDOUT) install test


bin/tags: $(BUILDOUT_BIN) $(BUILDOUT_CFG) setup.py
	$(BUILDOUT) install tags


clean_buildout:
	$(RM) -r bin
	$(RM) -r parts
	$(RM) -r develop-eggs
	$(RM) .installed.cfg
	$(RM) -r build
	$(RM) -r dist


clean_eggs:
	$(RM) -r download-cache
	$(RM) -r *.egg-info
	$(RM) -r eggs


clean: clean_buildout
	find turnip -name '*.py[co]' -print0 | xargs -r0 $(RM)
	find -maxdepth 1 -name '*.py[co]' -print0 | xargs -r0 $(RM)

clean_all: clean_buildout clean_eggs


.PHONY: \
    build build-update-paths check clean clean_all clean_buildout \
    clean_eggs default dist
