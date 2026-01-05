from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from schemas.config_handler import CustomerData, DataPSB, CustomerwithInvoices
from core.config import settings
from services.biling_scaper import BillingScraper, NOCScrapper
from services.supabase_client import get_customers_view, search_customers

router = APIRouter()

def get_scraper() -> NOCScrapper:
    try:
        return NOCScrapper()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"NMS unavailable: {e}")

def get_billing(nms: NOCScrapper = Depends(get_scraper)) -> BillingScraper:
    try:
        return BillingScraper(session=nms.session)
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Billing unavailable: {e}")

# Endpoint show psb avaible
@router.get("/psb", response_model=List[DataPSB])
def get_psb_data(scraper: NOCScrapper = Depends(get_scraper)):
    return scraper._get_data_psb()

# Endpoint send invoices
@router.get("/invoices", response_model=List[CustomerwithInvoices])
def get_fast_customer_details(
    query: str = Query(..., min_length=1),
    billing_scraper: BillingScraper = Depends(get_billing),
):
    customers = billing_scraper.search(query)
    if not customers:
        raise HTTPException(status_code=404, detail=f"No customer found for query: '{query}'")
    for customer in customers:
        if cid := customer.get("id"):
            detail_url = settings.DETAIL_URL_BILLING.format(cid)
            invoice_payload = billing_scraper.get_invoice_data(detail_url)
            customer.update(invoice_payload)
            customer["detail_url"] = detail_url
    return customers


@router.get("/customers-data", response_model=List[CustomerData])
async def get_customer_data(
    search: Optional[str] = Query(None, description="Search by name, address, or pppoe"),
    limit: int = Query(50, ge=1, le=200)
):
    """Get customer data from Supabase."""
    try:
        if search:
            customers = search_customers(search, limit=limit)
        else:
            customers = get_customers_view(limit=limit)
        
        # Map to CustomerData schema
        result = []
        for c in customers:
            result.append(CustomerData(
                name=c.get("nama", "Unknown"),
                address=c.get("alamat", ""),
                pppoe_user=c.get("user_pppoe", "")
            ))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch customers: {e}")