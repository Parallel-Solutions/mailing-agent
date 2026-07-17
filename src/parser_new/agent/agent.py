"""
agent/agent.py
"""
from __future__ import annotations

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent

from src.parser_new.agent.prompt import SYSTEM_PROMPT
from src.parser_new.logger import logger
from src.parser_new import config


def _build_llm():
    return ChatOpenAI(
        model=config.AGENT_MODEL,
        api_key=config.ANTHROPIC_API_KEY,
        base_url=config.LLM_BASE_URL,
        temperature=0,
        max_tokens=4096,
    )


def _build_tools() -> list:
    tools = []

    from src.parser_new.tools.search_tool import search_tool, search_deep_tool, search_official_site_tool
    tools.extend([search_tool, search_deep_tool, search_official_site_tool])

    from src.parser_new.tools.scraper_tool import scraper_tool, scraper_contacts_tool, scraper_links_tool, rusprofile_tool
    tools.extend([scraper_tool, scraper_contacts_tool, scraper_links_tool, rusprofile_tool])

    from src.parser_new.tools.maps_tool import geocode_tool, search_nearby_tool, search_2gis_tool
    tools.extend([geocode_tool, search_nearby_tool, search_2gis_tool])

    from src.parser_new.tools.oktmo_tool import (build_region_mo_file_tool, oktmo_region_list_tool, build_okrugs_file_tool,)
    tools.extend([build_region_mo_file_tool, oktmo_region_list_tool, build_okrugs_file_tool])

    from src.parser_new.tools.excel_tool import (
        read_excel_tool, write_excel_tool,
        append_excel_tool, update_excel_tool,
    )
    tools.extend([read_excel_tool, write_excel_tool, append_excel_tool, update_excel_tool])

    from src.parser_new.memory.memory_manager import (
        memory_add_rule_tool, memory_remember_error_tool,
        memory_save_experience_tool, memory_get_context_tool,
    )
    tools.extend([
        memory_add_rule_tool, memory_remember_error_tool,
        memory_save_experience_tool, memory_get_context_tool,
    ])

    from src.parser_new.tools.checko_tool import checko_company_tool, checko_search_tool
    tools.extend([checko_company_tool, checko_search_tool])

    # from src.parser_new.tools.gov_tool import gov_tool
    # tools.append(gov_tool)

    from src.parser_new.tools.batch_tool import batch_search_tool
    tools.append(batch_search_tool)

    from src.parser_new.tools.discovery_tool import discover_companies_tool
    tools.append(discover_companies_tool)

    from src.parser_new.tools.email_tool import fix_emails_tool
    tools.append(fix_emails_tool)

    logger.info(f"Загружено инструментов: {len(tools)}")
    return tools


def build_agent():
    llm   = _build_llm()
    tools = _build_tools()

    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=SYSTEM_PROMPT,
    )

    logger.info("Агент успешно собран")
    return agent


def format_chat_history(messages: list[dict]) -> list:
    history = []
    for msg in messages:
        if msg["role"] == "user":
            history.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            history.append(AIMessage(content=msg["content"]))
    return history