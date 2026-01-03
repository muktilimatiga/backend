from fastapi import APIRouter, UploadFile, File, HTTPException
from services.exceltopostgress import ExcelHandler

router = APIRouter()

# Handle ExceltoDatabases
@router.post("/exceltodb")
def upload_excel(file: UploadFile = File(...)):
    """
    Upload an Excel file (.xlsx) to sync fiber customer data.
    """
    if not file.filename.endswith(('.xls', '.xlsx')):
        raise HTTPException(status_code=400, detail="Invalid file type. Please upload .xlsx or .xls")

    try:
        # Pass the file-like object directly to pandas
        total_rows = ExcelHandler.process_file(file.file)
        
        return {
            "status": "success",
            "filename": file.filename,
            "rows_processed": total_rows,
            "message": "Data upserted successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")
