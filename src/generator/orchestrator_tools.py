from __future__ import annotations


ORCHESTRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_system_readiness",
            "description": "Check if required files, templates and current state are sufficient for the next step.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_status_summary",
            "description": "Get a compact summary of the current parser, generator, philologist and sender states.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_generator",
            "description": "Generate documents from the current data.xlsx input.",
            "parameters": {
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Reserved flag for future forced regeneration behavior.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_parser",
            "description": "Run parser/dofill queue processing to enrich missing data such as emails.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Optional limit for how many parser tasks to process.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_philologist",
            "description": "Run philologist check for generated documents using AI-enabled review.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sender_dry_run",
            "description": "Run sender in dry-run mode to check sending readiness without sending emails.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Optional limit for how many rows to inspect.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sender_send",
            "description": "Send emails for prepared rows, but only after explicit user confirmation of recipients.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Optional limit for how many rows to send.",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_output_archive_link",
            "description": "Provide a download link for the generated output archive if files are available.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]
