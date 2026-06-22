.PHONY: test demo agents orgs plan run clean

# Zero third-party deps; stdlib unittest.
test:
	python3 -m unittest discover -s tests -t .

demo:
	python3 examples/sample_run.py

agents:
	python3 -m tehai agents

orgs:
	python3 -m tehai orgs

# Example: make plan REQ="implement login validation"
plan:
	python3 -m tehai plan "$(REQ)"

# Example: make run REQ="add a helper"
run:
	python3 -m tehai run "$(REQ)" --limit 2

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -f runs/*.jsonl
