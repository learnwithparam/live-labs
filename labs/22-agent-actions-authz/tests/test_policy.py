"""The policy table is the truth: every cell, asked of the real Cerbos, must come back as the table says."""

from wrenfield.policy import ROLES, PromptOnly, call_attributes, check_table, table
from wrenfield.tools import TOOLS, destructive, read_only

ANA = {"id": "ana.costa", "role": "intern"}
PRIYA = {"id": "priya.shah", "role": "engineer"}
MARCUS = {"id": "marcus.lee", "role": "release_manager"}


def test_cerbos_agrees_with_every_cell_of_the_policy_table(policy):
    cells = check_table(policy)
    assert len(cells) == len(table()) * len(ROLES)
    wrong = [cell for cell in cells if not cell["ok"]]
    assert not wrong, wrong


def test_every_tool_is_in_the_table_and_marked_one_way():
    covered = {row["tool"] for row in table()}
    for spec in TOOLS:
        assert spec["name"] in covered, spec["name"]
        # A tool cannot be both: read-only means nothing changes, destructive means a change may not undo.
        assert not (read_only(spec["name"]) and destructive(spec["name"])), spec["name"]


def test_no_role_runs_a_destructive_tool_without_a_person(policy):
    for row in table():
        if not destructive(row["tool"]):
            continue
        for role in ROLES:
            decision = policy.check({"id": "probe", "role": role}, call_attributes(row["tool"], {"branch": row["branch"], "environment": row["environment"]}))
            assert decision.verdict != "allow", (row["case"], role)


def test_reads_never_ask_anyone(policy):
    for spec in TOOLS:
        if read_only(spec["name"]):
            for person in (ANA, PRIYA, MARCUS):
                assert policy.check(person, call_attributes(spec["name"], {"environment": "prod"})).verdict == "allow"


def test_an_ask_names_its_rule_and_reason(policy):
    decision = policy.decide(MARCUS, "delete_branch", {"branch": "release/2.4"})
    assert decision.verdict == "ask"
    assert decision.rule.startswith("ask-") and decision.reason


def test_protection_comes_from_the_repo_settings_not_the_branch_name_the_model_writes(policy):
    # "origin/release/2.4" is the same protected branch; a prefix must not slip past the release/* rule.
    decision = policy.decide(PRIYA, "delete_branch", {"branch": "origin/release/2.4"})
    assert decision.verdict == "deny"
    assert "protected" in decision.reason.lower()


def test_an_engineer_may_not_run_a_prod_migration_or_force_push_main(policy):
    assert policy.decide(PRIYA, "run_migration", {"name": "0008_drop_legacy_rates", "environment": "prod"}).verdict == "deny"
    assert policy.decide(PRIYA, "force_push", {"source": "feature/bonus-calc", "branch": "main"}).verdict == "deny"
    assert policy.decide(MARCUS, "force_push", {"source": "feature/bonus-calc", "branch": "main"}).verdict == "deny"


def test_prompt_only_allows_everything_but_keeps_the_real_verdict(policy):
    decision = PromptOnly(policy).decide(ANA, "run_sql", {"statement": "DROP TABLE legacy_rates", "environment": "prod"})
    assert decision.verdict == "allow"
    assert decision.policy_verdict == "deny"
