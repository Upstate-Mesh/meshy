PYTHON = ~/py_envs/bin/python
BIN    = ~/py_envs/bin

.PHONY: run format deps test

deps:
	$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) main.py

test:
	$(PYTHON) -m pytest main_spec.py -v

format:
	$(BIN)/black .
	$(BIN)/isort --profile black .
	$(BIN)/yamlfix .
	$(BIN)/mdformat .
	$(BIN)/pylint --rcfile .pylintrc --recursive=y ./
