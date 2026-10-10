PYTHON ?= python3

.PHONY: build diagnostics test validate validate3 state state-diagnostics baseline baseline-diagnostics strong strong-ordinary steady

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

state:
	$(PYTHON) experiments/build_state.py

state-diagnostics:
	$(PYTHON) experiments/build_state.py --diagnostics

baseline:
	$(PYTHON) experiments/build_baseline.py

baseline-diagnostics:
	$(PYTHON) experiments/build_baseline.py --diagnostics

strong:
	$(PYTHON) experiments/build_strong.py --format common

strong-ordinary:
	$(PYTHON) experiments/build_strong.py --format task --ordinary

steady:
	$(PYTHON) experiments/build_steady_bmv2.py --output build/bmv2-steady --jobs 2
