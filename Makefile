# check: no key needed; the structure of every lab plus each ready lab's keyless tests.
# proof: one lab against a real model; LLM_API_KEY in the environment or ENV_FILE=path.
LABS := $(sort $(wildcard labs/*/))

.PHONY: check readme proof scene

check:
	python3 scripts/check_course.py
	cd scripts && uv run --with pytest pytest -q -p no:cacheprovider test_scripts.py
	@for lab in $(LABS); do echo "== $$lab"; if [ -f $$lab/package.json ]; then (cd $$lab && npm ci --silent && npm test --silent) || exit 1; else (cd $$lab && uv run --frozen pytest -q) || exit 1; fi; done

readme:
	python3 scripts/check_course.py --write

proof:
	@test -n "$(LAB)" || (echo "usage: make proof LAB=01-structured-output [ENV_FILE=path]"; exit 2)
	python3 scripts/walkthrough.py labs/$(LAB) $(if $(ENV_FILE),--env-file $(ENV_FILE))

scene:
	@python3 scripts/walkthrough.py labs/$(LAB) --scene
