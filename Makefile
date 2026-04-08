.PHONY: run format deps test

deps:
	python3 -m pip install -r requirements.txt

run:
	python3 main.py

test:
	python3 -m pytest main_spec.py -v

format:
	black .
	isort --profile black .
	yamlfix .
	mdformat .
	pylint --rcfile .pylintrc --recursive=y ./
