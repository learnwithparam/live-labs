"""The agent's tools, each marked read-only or destructive the way MCP tool annotations do.

MCP lets a server say `readOnlyHint` (the tool changes nothing) and `destructiveHint` (the change may
not be undone). Goose reads those hints to decide what to confirm. Here the hints are attributes the
policy reads, so the code that marks a tool and the rule that guards it stay separate.
"""


def tool(tool_name: str, description: str, read_only: bool, destructive: bool, **properties: dict) -> dict:
    return {"name": tool_name, "description": description,
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive},
            "parameters": {"type": "object", "properties": properties, "required": list(properties),
                           "additionalProperties": False}}


ENVIRONMENT = {"type": "string", "enum": ["dev", "prod"], "description": "Which database: dev or prod."}
TOOLS = [
    tool("list_files", "List every file in the repo.", True, False),
    tool("read_file", "Read one file from the repo.", True, False,
         path={"type": "string", "description": "Path from the repo root, like payroll/tax.py."}),
    tool("grep", "Search the repo's files with a regular expression; prints file:line: text.", True, False,
         pattern={"type": "string", "description": "An extended regular expression."}),
    tool("list_branches", "List local branches and the branches on origin, with each one's last commit.", True, False),
    tool("run_tests", "Run the repo's unit tests and print the result.", True, False),
    tool("query_db", "Run one read-only SELECT on the dev or prod database; prints up to 50 rows.", True, False,
         sql={"type": "string", "description": "A SELECT statement."}, environment=ENVIRONMENT),
    # A write that can be undone from git is not destructive, but the policy may still ask about it.
    tool("write_file", "Create or replace one file in the working tree.", False, False,
         path={"type": "string", "description": "Path from the repo root."},
         content={"type": "string", "description": "The whole new content of the file."}),
    tool("commit", "Commit every change in the working tree to the current local branch.", False, False,
         message={"type": "string", "description": "The commit message."}),
    tool("delete_branch", "Delete a branch locally and on origin.", False, True,
         branch={"type": "string", "description": "The branch name, like spike/old-export."}),
    tool("force_push", "Force-push a local branch to a branch on origin, replacing its history.", False, True,
         source={"type": "string", "description": "The local branch to push."},
         branch={"type": "string", "description": "The branch on origin to overwrite."}),
    tool("run_migration", "Apply one SQL file from migrations/ to the dev or prod database.", False, True,
         name={"type": "string", "description": "The migration file name, like 0007_add_bonus_to_payslips."},
         environment=ENVIRONMENT),
    tool("run_sql", "Run any SQL that changes the dev or prod database: DROP, DELETE, UPDATE, ALTER.", False, True,
         statement={"type": "string", "description": "The SQL to run."}, environment=ENVIRONMENT),
]
BY_NAME = {spec["name"]: spec for spec in TOOLS}


def openai_tools() -> list[dict]:
    """The same tools in the chat API's format. The model sees names, descriptions and schemas only."""
    return [{"type": "function", "function": {key: spec[key] for key in ("name", "description", "parameters")}}
            for spec in TOOLS]


def read_only(name: str) -> bool:
    return BY_NAME[name]["annotations"]["readOnlyHint"]


def destructive(name: str) -> bool:
    return BY_NAME[name]["annotations"]["destructiveHint"]
