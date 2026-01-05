# /api/v1/endpoints/psb.py
"""
PSB (Pasang Baru) API endpoints for Telegram bot integration.
"""

from fastapi import APIRouter, Query
from typing import List, Optional
from pydantic import BaseModel

# Import from existing supabase client
from services.supabase_client import search_customers, get_customers_view

router = APIRouter()


class PSBCustomer(BaseModel):
    """PSB Customer schema."""
    customer_id: int
    nama: str
    alamat: Optional[str] = None
    user_pppoe: Optional[str] = None
    paket: Optional[str] = None
    olt_name: Optional[str] = None
    
    class Config:
        from_attributes = True


@router.get("/psb", response_model=List[PSBCustomer])
async def get_psb_list(
    search: Optional[str] = Query(None, description="Search by name, address, or pppoe"),
    limit: int = Query(50, ge=1, le=200, description="Max items to return")
):
    """
    Get list of PSB (Pasang Baru) candidates.
    
    - If search is provided, filters by nama, alamat, or user_pppoe
    - Returns customers from customers_view
    """
    try:
        if search:
            customers = search_customers(search, limit=limit)
        else:
            customers = get_customers_view(limit=limit)
        
        # Map to PSBCustomer schema
        result = []
        for c in customers:
            result.append(PSBCustomer(
                customer_id=c.get("customer_id", 0),
                nama=c.get("nama", "Unknown"),
                alamat=c.get("alamat"),
                user_pppoe=c.get("user_pppoe"),
                paket=c.get("paket"),
                olt_name=c.get("olt_name")
            ))
        
        return result
        
    except Exception as e:
        # Return empty list on error (bot handles empty list gracefully)
        return []
