# check: no key needed; the structure of every lab plus each ready lab's keyless tests.
# proof: one lab against a real model; LLM_API_KEY in the environment or ENV_FILE=path.
LABS := $(sort $(wildcard labs/*/))

.PHONY: check readme proof proof-all scene mermaid

check: mermaid
	python3 scripts/check_course.py
	cd scripts && uv run --with pytest pytest -q -p no:cacheprovider test_scripts.py
	@for lab in $(LABS); do echo "== $$lab"; if [ -f $$lab/package.json ]; then (cd $$lab && npm ci --silent && npm test --silent) || exit 1; else (cd $$lab && uv run --frozen pytest -q) || exit 1; fi; done

mermaid:
	npm ci --silent
	node scripts/check-mermaid.mjs $$(git ls-files -co --exclude-standard '*.md')

readme:
	python3 scripts/check_course.py --write

proof:
	@test -n "$(LAB)" || (echo "usage: make proof LAB=01-structured-output [ENV_FILE=path]"; exit 2)
	python3 scripts/walkthrough.py labs/$(LAB) $(if $(ENV_FILE),--env-file $(ENV_FILE))

# Re-proves every lab, serially: for a model or provider change. One edited lab only needs `make proof LAB=<slug>`.
proof-all:
	@for lab in $(LABS); do $(MAKE) proof LAB=$$(basename $$lab) $(if $(ENV_FILE),ENV_FILE=$(ENV_FILE)) || exit 1; done

scene:
	@python3 scripts/walkthrough.py labs/$(LAB) --scene
