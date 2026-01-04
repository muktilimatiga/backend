from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
import re
import asyncio
from core.config import settings
from schemas.onu_handler import (
    OnuDetailRequest, OnuDetailResponse, OnuFullResponse
)

from services.telnet import TelnetClient
from services.connection_manager import olt_manager
from core.olt_config import OLT_OPTIONS, get_olt_info

router = APIRouter()

def _parse_interface(interface: str) -> str:
    """
    Parsing interface from example 1/1/1:1 to 1/1/1
    """
    # Pattern looks for: numbers / numbers / numbers : numbers
    pattern = r".*(\d+/\d+/\d+):(\d+)"
    
    match = re.search(pattern, interface)
    if not match:
         raise ValueError(f"Invalid interface format: {interface}")
    
    # Return the port part (Group 1)
    return interface.split(":")[0]
    

@router.post("/onu/cek", response_class=PlainTextResponse)
async def cek_onu(request: OnuDetailRequest):
    olt_info = get_olt_info(request.olt_name)
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT {request.olt_name} tidak ditemukan!")
    
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
            
            # 1. Get the data (Ensure NO commas at the end!)
        detail_data = await handler.get_onu_detail(request.interface)
        attenuation = await handler.get_attenuation(request.interface)
        
        # 2. Return the CORRECT response model
        # Do NOT return OnuDetailResponse here!
        return PlainTextResponse(
            content=f"Detail Data:\n{detail_data}\n\n Attenuation Data:\n {attenuation}")
    
    except Exception as e:
        # This catches the OLT error text if it wasn't caught inside TelnetClient
        raise HTTPException(status_code=500, detail=f"Proses cek gagal: {e}")
    
@router.post("/{olt_name}/onu/reboot")
async def reboot_onu(request: OnuDetailRequest):
    olt_info = get_olt_info(request.olt_name)
    if not olt_info:
        raise HTTPException(
            status_code=404, 
            detail=f"OLT {request.olt_name} tidak ditemukan!")
    
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
        data = await handler.send_reboot_command(request.interface)
        
        return OnuDetailResponse(result=data)
    
    except (ConnectionError, asyncio.TimeoutError) as e:
        raise HTTPException(status_code=504, detail=f"Gagal terhubung atau timeout saat koneksi ke OLT: {e}")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proses cek gagal: {e}")
    
@router.post("/{olt_name}/onu/no-onu", response_model=OnuDetailResponse)
async def no_onu(request: OnuDetailRequest):
    olt_info = get_olt_info(request.olt_name)
    if not olt_info:
        raise HTTPException(
            status_code=404, 
            detail=f"OLT {request.olt_name} tidak ditemukan!")
    
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
        data = await handler.send_no_onu(request.interface)
        
        return OnuDetailResponse(result=data)
    
    except (ConnectionError, asyncio.TimeoutError) as e:
        raise HTTPException(status_code=504, detail=f"Gagal terhubung atau timeout saat koneksi ke OLT: {e}")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proses cek gagal: {e}")
    

@router.post("/{olt_name}/onu/port_state", response_class=PlainTextResponse)
async def cek_1_port(request: OnuDetailRequest):
    olt_info = get_olt_info(request.olt_name)
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT {request.olt_name} tidak ditemukan!")
    
    # 2. Minta koneksi ke Manager (Bukan bikin baru)
    # Manager akan kasih koneksi lama kalau ada, atau bikin baru kalau belum ada
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
        
        # 3. Langsung pakai handler (tanpa indentasi 'async with')
        base_interface = _parse_interface(request.interface)
        data = await handler.get_gpon_onu_state(base_interface)

        return PlainTextResponse(content=data)

    except Exception as e:
        # Jangan lupa handle error, tapi jangan close connection di sini 
        # (biarkan manager yang urus reconnect nanti)
        raise HTTPException(status_code=500, detail=str(e))
    

@router.post("/{olt_name}/onu/port_rx", response_class=PlainTextResponse)
async def cek_1_port_rx(request: OnuDetailRequest):
    olt_info = get_olt_info(request.olt_name)
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT {request.olt_name} tidak ditemukan!")
    
    # 2. Minta koneksi ke Manager (Bukan bikin baru)
    # Manager akan kasih koneksi lama kalau ada, atau bikin baru kalau belum ada
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
        
        # 3. Langsung pakai handler (tanpa indentasi 'async with')
        base_interface = _parse_interface(request.interface)
        data = await handler.get_onu_rx(base_interface)

        return PlainTextResponse(content=data)

    except Exception as e:
        # Jangan lupa handle error, tapi jangan close connection di sini 
        # (biarkan manager yang urus reconnect nanti)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{olt_name}/onu/get-ip", response_model=OnuDetailResponse)
async def get_onu_ip(request: OnuDetailRequest):
    olt_info = get_olt_info(request.olt_name)
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT {request.olt_name} tidak ditemukan!")
    
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
        
        # ONU-level command: needs full interface with ONU ID (1/1/1:1)
        data = await handler.get_onu_ip_host(request.interface)

        return OnuDetailResponse(result=data)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{olt_name}/onu/cek-eth")
async def cek_eth(request: OnuDetailRequest):
    olt_info = get_olt_info(request.olt_name)
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT {request.olt_name} tidak ditemukan!")
    
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
        
        # ONU-level command: needs full interface with ONU ID (1/1/1:1)
        data = await handler.get_eth_port_statuses(request.interface)

        # Returns list of dicts with lan_detected, is_unlocked, etc.
        return data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{olt_name}/onu/get-dba", response_model=OnuDetailResponse)
async def get_dba(request: OnuDetailRequest):
    olt_info = get_olt_info(request.olt_name)
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT {request.olt_name} tidak ditemukan!")
    
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
        
        base_interface = _parse_interface(request.interface)
        data = await handler.get_onu_dba(base_interface)

        return OnuDetailResponse(result=data)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    