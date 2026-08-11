.PHONY: install run trace test adk clean

install:
	pip install -e ".[dev]"

run:            ## the graph, offline, printing the council brief
	python -m sangam --date 2026-08-11

trace:          ## same, with every node event on stderr
	python -m sangam --date 2026-08-11 --trace

live:           ## live extraction path (needs GOOGLE_API_KEY)
	python -m sangam --date 2026-08-11 --live

test:
	pytest

adk:            ## ADK dev UI, step the graph node by node
	adk web

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} + ; rm -rf .pytest_cache
