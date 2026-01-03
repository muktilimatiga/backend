from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query
from core.config import settings
from schemas.customers_scrapper import CustomerwithInvoices, DataPSB
from services.biling_scaper import BillingScraper, NOCScrapper

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