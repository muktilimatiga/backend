import subprocess
import asyncio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from typing import Dict, List
from starlette.responses import StreamingResponse
from core.config import settings
import httpx
import logging
from fastapi import APIRouter, HTTPException
from schemas.open_ticket import TicketClosePayload
from services.open_ticket import (
    create_ticket_as_cs,
    process_ticket_as_noc,
    close_ticket_as_noc,
    forward_ticket_as_noc,
    extract_search_results,
    build_driver,
    maybe_login,
    search_user
)
from core.config import settings

from typing import List
from fastapi import APIRouter, Query, HTTPException, Depends

from schemas.customers_scrapper import Customer, DataPSB, CustomerwithInvoices
from services.biling_scaper import BillingScraper

import pprint
from core.config import settings


__all__ = ["APIRouter", "HTTPException", "Request", "WebSocket", "Dict", "List", "StreamingResponse", "httpx", "websockets", "logging", "APIRouter", "HTTPException", "OpenTicketRequest", "TicketClosePayload", "ForwardTicketPayload", "ProcessTicketRequest", "create_ticket_as_cs", "process_ticket_as_noc", "close_ticket_as_noc", "forward_ticket_as_noc", "extract_search_results", "build_driver", "maybe_login", "search_user", "WebSocketDisconnect"]