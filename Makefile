.PHONY: clean clean-logs clean-cache

clean: clean-logs clean-cache

clean-logs:
	find data/logs -name '*.log' -o -name '*.log.*' | xargs truncate -s 0 2>/dev/null || true

clean-cache:
	find . -path './.venv' -prune -o -type d -name '__pycache__' -print | xargs rm -rf
	rm -rf .pytest_cache *.egg-info
