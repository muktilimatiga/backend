#/api/v1/endpoints/config

from fastapi import APIRouter, HTTPException
from typing import List
import asyncio

from core.config import settings
from schemas.config_handler import (
    UnconfiguredOnt, ConfigurationRequest, ConfigurationResponse, 
    ConfigurationSummary, OptionsResponse, ConfigurationBridgeRequest, 
    CongigurationBridgeResponse, BatchConfigurationRequest, 
    BatchItemResult, BatchConfigurationResponse
)
from services.telnet import TelnetClient
from services.connection_manager import olt_manager
from core.olt_config import OLT_OPTIONS, MODEM_OPTIONS, PACKAGE_OPTIONS

router = APIRouter()

@router.get("/api/options", response_model=OptionsResponse)
async def get_options():
    """Mengembalikan semua opsi yang dibutuhkan untuk form di frontend."""
    return {
        "olt_options": list(OLT_OPTIONS.keys()),
        "modem_options": MODEM_OPTIONS,
        "package_options": list(PACKAGE_OPTIONS.keys())
    }

@router.get("/api/olts/{olt_name}/detect-onts", response_model=List[UnconfiguredOnt])
async def detect_uncfg_onts(olt_name: str):
    """Mendeteksi semua unconfigured ONT pada OLT yang dipilih."""
    olt_info = OLT_OPTIONS.get(olt_name.upper())
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT '{olt_name}' tidak ditemukan.")
    
    try:
        handler = await olt_manager.get_connection(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        )
        
        ont_list = await handler.find_unconfigured_onts()
        return ont_list
    except ConnectionError as e:
        raise HTTPException(status_code=504, detail=f"Gagal terhubung ke OLT: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Terjadi error internal: {e}")

@router.post("/api/olts/{olt_name}/configure", response_model=ConfigurationResponse)
async def run_configuration(olt_name: str, request: ConfigurationRequest):
    """Menjalankan proses konfigurasi untuk satu ONT."""
    olt_info = OLT_OPTIONS.get(olt_name.upper())
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT '{olt_name}' tidak ditemukan.")
        
    try:
        async with TelnetClient(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        ) as handler:
            logs, summary = await handler.apply_configuration(request, vlan=olt_info["vlan"])
            logs.append("INFO < Database save functionality not yet implemented.")
            
            return ConfigurationResponse(
                message="Konfigurasi berhasil.",
                summary=ConfigurationSummary(**summary),
                logs=logs
            )

            # ---
    except (ConnectionError, asyncio.TimeoutError) as e:
        raise HTTPException(status_code=504, detail=f"Gagal terhubung atau timeout saat koneksi ke OLT: {e}")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proses konfigurasi gagal: {e}")
    
@router.post("api/olts/{olt_name}/config_bridge", response_model=CongigurationBridgeResponse)
async def run_configuration_bridge(olt_name: str, request: ConfigurationBridgeRequest):
    "Menjalankan konfigurasi bridge"
    olt_info = OLT_OPTIONS.get(olt_name.upper())
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT '{olt_name}' tidak ditemukan.")
    
    try:
        async with TelnetClient(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]

        ) as handler: 
            logs, summary = await handler.config_bridge(request)
            logs.append("INFO < Database save functionality not yet implemented.")
            
        return ConfigurationResponse(
            message="Konfigurasi Berhasil",
            summary=ConfigurationSummary(**summary),
            logs=logs
        )
    
    except (ConnectionError, asyncio.TimeoutError) as e:
        raise HTTPException(status_code=504, detail=f"Gagal terhubung atau timeout saat koneksi ke OLT: {e}")
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proses konfigurasi gagal: {e}")
    

@router.post("/api/olts/{olt_name}/configure/batch", response_model=BatchConfigurationResponse)
async def run_batch_configuration(olt_name: str, batch: BatchConfigurationRequest):
    """Menjalankan konfigurasi untuk BANYAK ONT dalam satu koneksi Telnet."""
    
    # 1. Validate OLT exists
    olt_info = OLT_OPTIONS.get(olt_name.upper())
    if not olt_info:
        raise HTTPException(status_code=404, detail=f"OLT '{olt_name}' tidak ditemukan.")

    results = []
    success_count = 0
    fail_count = 0

    try:
        # 2. Open Telnet Connection ONCE
        async with TelnetClient(
            host=olt_info["ip"],
            username=settings.OLT_USERNAME,
            password=settings.OLT_PASSWORD,
            is_c600=olt_info["c600"]
        ) as handler:
            
            # 3. Loop through the batch items using the SAME handler
            for request_item in batch.items:
                # Use SN or Username as identifier for the report
                item_id = getattr(request_item, 'sn', 'Unknown') 
                
                try:
                    # Apply config
                    logs, summary = await handler.apply_configuration(request_item, vlan=olt_info["vlan"])
                    
                    # Log success
                    results.append(BatchItemResult(
                        identifier=item_id,
                        success=True,
                        message="Konfigurasi berhasil.",
                        logs=logs
                    ))
                    success_count += 1
                    
                except Exception as e:
                    # 4. Catch errors per item so one failure doesn't stop the whole batch
                    fail_count += 1
                    results.append(BatchItemResult(
                        identifier=item_id,
                        success=False,
                        message=str(e),
                        logs=[f"Error processing {item_id}: {str(e)}"]
                    ))

    except (ConnectionError, asyncio.TimeoutError) as e:
        # If the MAIN connection fails, the whole batch fails
        raise HTTPException(status_code=504, detail=f"Critical: Gagal koneksi ke OLT: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"System Error: {e}")

    # 5. Return aggregated results
    return BatchConfigurationResponse(
        total=len(batch.items),
        success_count=success_count,
        fail_count=fail_count,
        results=results
    )