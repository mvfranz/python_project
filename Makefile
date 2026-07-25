.ONESHELL:
ENV_PREFIX=$(shell python -c "if __import__('pathlib').Path('.venv/bin/pip').exists(): print('.venv/bin/')")
USING_POETRY=$(shell grep "tool.poetry" pyproject.toml && echo "yes")

.PHONY: help
help:             ## Show the help.
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@fgrep "##" Makefile | fgrep -v fgrep


.PHONY: show
show:             ## Show the current environment.
	@echo "Current environment:"
	@if [ "$(USING_POETRY)" ]; then poetry env info && exit; fi
	@echo "Running using $(ENV_PREFIX)"
	@$(ENV_PREFIX)python -V
	@$(ENV_PREFIX)python -m site

.PHONY: install
install:          ## Install the project in dev mode.
	@if [ "$(USING_POETRY)" ]; then poetry install && exit; fi
	@echo "Don't forget to run 'make virtualenv' if you got errors."
	$(ENV_PREFIX)pip install -e .[test]

.PHONY: fmt
fmt:              ## Format code using ruff.
	$(ENV_PREFIX)python -m ruff format src/ tests/
	$(ENV_PREFIX)python -m ruff check --fix src/ tests/

.PHONY: lint
lint:             ## Run ruff and mypy linters.
	$(ENV_PREFIX)python -m ruff check src/ tests/
	$(ENV_PREFIX)python -m ruff format --check src/ tests/
	$(ENV_PREFIX)python -m mypy src/modplus

.PHONY: test
test: lint        ## Run tests and generate coverage report.
	$(ENV_PREFIX)python -m pytest -v --cov=modplus -l --tb=short --maxfail=1 tests/
	$(ENV_PREFIX)python -m coverage xml
	$(ENV_PREFIX)python -m coverage html

.PHONY: examples
examples:         ## Run every example program under examples/.
	@for f in examples/*.m2p; do \
		case "$$f" in \
			examples/mathutils.m2p) continue ;; \
			examples/import_demo.m2p) \
				echo "=== $$f (+ mathutils.m2p) ==="; \
				$(ENV_PREFIX)python -m modplus.cli run "$$f" examples/mathutils.m2p || exit 1 ;; \
			*) \
				echo "=== $$f ==="; \
				$(ENV_PREFIX)python -m modplus.cli run "$$f" || exit 1 ;; \
		esac; \
	done

.PHONY: watch
watch:            ## Run tests on every change.
	ls **/**.py | entr $(ENV_PREFIX)python -m pytest -s -vvv -l --tb=long --maxfail=1 tests/

.PHONY: clean
clean:            ## Clean unused files.
	@find ./ -name '*.pyc' -exec rm -f {} \;
	@find ./ -name '__pycache__' -exec rm -rf {} \;
	@find ./ -name 'Thumbs.db' -exec rm -f {} \;
	@find ./ -name '*~' -exec rm -f {} \;
	@rm -rf .cache
	@rm -rf .pytest_cache
	@rm -rf .mypy_cache
	@rm -rf build
	@rm -rf dist
	@rm -rf *.egg-info
	@rm -rf htmlcov
	@rm -rf .tox/
	@rm -rf docs/_build

.PHONY: virtualenv
virtualenv:       ## Create a virtual environment.
	@if [ "$(USING_POETRY)" ]; then poetry install && exit; fi
	@echo "creating virtualenv ..."
	@rm -rf .venv
	@python3 -m venv .venv
	@./.venv/bin/pip install -U pip
	@./.venv/bin/pip install -e .[test]
	@echo
	@echo "!!! Please run 'source .venv/bin/activate' to enable the environment !!!"

.PHONY: release
release:          ## Create a new tag for release.
	@echo "WARNING: This operation will create a version tag and push to github"
	@read -p "Version? (provide the next x.y.z semver) : " TAG
	@sed -i "s/^version = \".*\"/version = \"$${TAG}\"/" pyproject.toml
	@$(ENV_PREFIX)python -m gitchangelog > HISTORY.md
	@git add pyproject.toml HISTORY.md
	@git commit -m "release: version $${TAG} 🚀"
	@echo "creating git tag : $${TAG}"
	@git tag $${TAG}
	@git push -u origin HEAD --tags
	@echo "Github Actions will detect the new tag and release the new version."

.PHONY: docs
docs:             ## Build the documentation.
	@echo "building documentation ..."
	@$(ENV_PREFIX)python -m mkdocs build
	URL="site/index.html"; xdg-open $$URL || sensible-browser $$URL || x-www-browser $$URL || gnome-open $$URL


# This project has been generated from rochacbruno/python-project-template
# __author__ = 'rochacbruno'
# __repo__ = https://github.com/rochacbruno/python-project-template
# __sponsor__ = https://github.com/sponsors/rochacbruno/
