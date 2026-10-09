PYTHON ?= python3

.PHONY: build diagnostics test validate validate3

build:
	$(PYTHON) experiments/build.py

diagnostics:
	$(PYTHON) experiments/build.py --diagnostics

test:
	$(PYTHON) -m unittest discover -s tests -v

validate:
	sudo $(PYTHON) experiments/run_validation.py --hops 5

validate3:
	sudo $(PYTHON) experiments/run_validation.py --hops 3
