PYTHON ?= python3

.PHONY: build diagnostics test validate validate3 state state-diagnostics baseline baseline-diagnostics

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
