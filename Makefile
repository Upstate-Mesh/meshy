.PHONY: run format deps

deps:
	python3 -m pip install -r requirements.txt

run:
	python3 main.py

format:
	black .
	isort --profile black .
	yamlfix .
	mdformat .
	pylint --rcfile .pylintrc --recursive=y ./
