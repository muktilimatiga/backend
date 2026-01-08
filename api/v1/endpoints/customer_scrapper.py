from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query

from core import settings
from schemas.config_handler import CustomerData
from schemas.customers_scrapper import DataPSB, CustomerwithInvoices, Customer, CustomerBillingInfo
from services.biling_scaper import BillingScraper, NOCScrapper
from services.supabase_client import search_customers

router = APIRouter()

def get_scraper() -> NOCScrapper:
    try:
        return NOCScrapper()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"NMS unavailable: {e}")

def get_billing() -> BillingScraper:
    """Create BillingScraper with its own session - billing requires separate auth from NMS."""
    try:
        return BillingScraper()  # Let it create its own session and login to billing
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Billing unavailable: {e}")

async def get_customer_data(query: str = Query(..., min_length=1),
    billing_scraper: BillingScraper = Depends(get_billing),
):
    customers = billing_scraper.search(query)
    if not customers:
        raise HTTPException(status_code=404, detail=f"No customer found for query: '{query}'")
    
    result = []
    for customer in customers:
        if cid := customer.get("id"):
            detail_url = settings.DETAIL_URL_BILLING.format(cid)
            invoice_payload = billing_scraper.get_invoice_data(detail_url)
            
            # Extract description from first invoice
            invoices = invoice_payload.get("invoices", [])
            description = invoices[0].get("description") if invoices else None
            
            # Extract last_payment from summary
            summary = invoice_payload.get("summary", {})
            last_payment = summary.get("last_paid_month")
            
            result.append(CustomerBillingInfo(
                name=customer.get("name"),
                address=customer.get("address"),
                user_pppoe=customer.get("user_pppoe"),
                last_payment=last_payment,
                description=description,
            ))
    return result
    

# Endpoint show psb avaible
@router.get("/psb", response_model=List[DataPSB])
def get_psb_data(scraper: NOCScrapper = Depends(get_scraper)):
    return scraper._get_data_psb()

# Endpoint send invoices
@router.get("/invoices", response_model=List[CustomerBillingInfo])
# def get_customer_invoices(
#     query: str = Query(..., min_length=1),
#     billing_scraper: BillingScraper = Depends(get_billing),
# ):
#     customers = billing_scraper.search(query)
#     if not customers:
#         raise HTTPException(status_code=404, detail=f"No customer found for query: '{query}'")
    
#     results = []
#     for customer in customers:
#         if cid := customer.get("id"):
#             detail_url = settings.DETAIL_URL_BILLING.format(cid)
#             invoice_payload = billing_scraper.get_invoice_data(detail_url)
            
#             # Extract description from first invoice
#             invoices = invoice_payload.get("invoices", [])
#             description = billing_scraper.get_invoice_data(detail_url)
            
#             # Extract last_payment from summary
#             summary = invoice_payload.get("summary", {})
#             last_payment = summary.get("last_paid_month")
            
#             results.append(CustomerBillingInfo(
#                 name=customer.get("name"),
#                 address=customer.get("address"),
#                 user_pppoe=customer.get("user_pppoe"),
#                 last_payment=last_payment,
#                 description=description,
#             ))
#     return results

# Get location coordinates and package
@router.get("/customers-billing", response_model=List[CustomerwithInvoices])
def get_customer_details(
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


# Get customer info only (no invoices) - coordinate, user_join, mobile, paket
@router.get("/customers-info")
def get_customer_info(
    query: str = Query(..., min_length=1),
    billing_scraper: BillingScraper = Depends(get_billing),
):
    """Get customer info without invoice data - faster for basic lookups."""
    customers = billing_scraper.search(query)
    if not customers:
        raise HTTPException(status_code=404, detail=f"No customer found for query: '{query}'")
    
    results = []
    for customer in customers:
        if cid := customer.get("id"):
            detail_url = settings.DETAIL_URL_BILLING.format(cid)
            # Get invoice data to extract customer info
            invoice_payload = billing_scraper.get_invoice_data(detail_url)
            
            # Format coordinate as Google Maps URL
            coordinate = invoice_payload.get("coordinate")
            maps_url = None
            if coordinate:
                maps_url = f"https://www.google.com/maps/search/?api=1&query={coordinate}"
            
            # Format mobile as WhatsApp URL
            mobile = invoice_payload.get("mobile")
            wa_url = None
            if mobile:
                wa_url = f"https://wa.me/{mobile}"
            
            results.append({
                "nama": customer.get("name"),
                "alamat": customer.get("address"),
                "user_pppoe": customer.get("user_pppoe"),
                "paket": invoice_payload.get("paket"),
                "maps": maps_url,
                "mobile": wa_url,
                "last_payment": invoice_payload.get("summary", {}).get("last_paid_month"),
                "user_join": invoice_payload.get("user_join"),
            })
    return results


@router.get("/customers-data", response_model=List[CustomerData])
async def get_customer_data(
    search: str = Query(..., min_length=1, description="Search by name, address, or pppoe")
):
    """Get customer data from Supabase."""
    try:
        customers = search_customers(search)
        
        # Map to CustomerData schema
        result = []
        for c in customers:
            result.append(CustomerData(
                name=c.get("nama", "Unknown"),
                address=c.get("alamat", ""),
                pppoe_user=c.get("user_pppoe", ""),
                pppoe_password=c.get("pppoe_password", ""),
                olt_name=c.get("olt_name", ""),
                interface=c.get("interface", ""),
                onu_sn=c.get("onu_sn", ""),
                modem_type=c.get("modem_type", ""),
            ))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch customers: {e}")